"""One-shot invocation: run a task through a harness and capture the result.

The primary interface::

    execute(role, harness, model_target, cwd, task) -> {status, output, error,
                                                        elapsed, pid,
                                                        request_id, ...}

``model_target`` is the ALREADY-RESOLVED model target supplied by Model
Allocator. This package never resolves or substitutes it — it only renders it
into the harness's native CLI and reports its identity. The result dict always
includes the original ``status``/``output``/``error``/``elapsed`` keys (for
backward compatibility) plus operational metadata: ``pid``, ``request_id``,
``harness``, ``role``, ``model_target``, ``payload_chars``, ``payload_lines``,
``payload_sha256``.

Subprocess invocation is argv-style: a 20k+ character multiline prompt is
passed to :class:`subprocess.Popen` as exactly one argv element with no shell
interpolation and no shlex round-trip, so embedded newlines in the prompt are
preserved verbatim and produce exactly one harness invocation.

Progress (RUNNING / periodic HEARTBEAT) is surfaced through an optional
``on_event`` callback without exposing private reasoning. While the harness
runs, its JSONL event stream is rendered live through the same callback as
``OUTPUT`` fragments (one pane line each, plain text plus a ``kind`` — prose
as it streams, runs of identical tool results coalesced into one ``×N``
line, informative status changes as they happen); the heartbeat then only
speaks when the pane has been silent for a whole interval, releasing a held
tool group first. The final SUCCESS or ERROR is reported by the terminal from
the returned result, not by ``on_event``.
"""

from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
import json
import threading
import time as _time

from .adapter import (
    build_aider_env,
    build_crush_env,
    build_goose_env,
    build_qwen_env,
    build_sweagent_env,
    build_task_argv,
)
from .definition import model_target_identity, resolve_harness, resolve_role_key
from .status import CANCELLED, ERROR, RUNNING, SUCCESS
from .transport import compute_identity, make_request_id

from .capabilities import LargeInputRefusedError
from .runspec import HarnessRunResult, RunEvidence, RunTiming, RunUsage

#: Time between heartbeat emissions while a harness subprocess stays alive.
DEFAULT_HEARTBEAT_INTERVAL = 15.0

# Ctrl+C is delivered to the terminal's foreground process group by the
# interactive driver.  We give a child a small, bounded opportunity to wind
# down, then escalate before declaring the process group gone.
CANCEL_GRACE_SECONDS = 1.0
_CANCEL_TERM_ESCALATION_FRACTION = 0.5


def _cancellation_requested(cancel_event, cancel_callback):
    if cancel_event is not None and cancel_event.is_set():
        return True
    if cancel_callback is None:
        return False
    try:
        return bool(cancel_callback())
    except (TypeError, RuntimeError, ValueError):
        return False


def _signal_process_group(proc, signum):
    """Signal only the subprocess group created for this invocation."""
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signum)
            return True
    except (AttributeError, OSError, ProcessLookupError):
        pass
    try:
        proc.send_signal(signum)
    except (AttributeError, OSError, ProcessLookupError):
        return False
    return True


def _reset_child_signal_handlers():
    """Restore normal child signal behaviour after fork.

    The terminal temporarily owns SIGINT.  A spawned harness must not inherit
    that handler, otherwise a group SIGINT would be swallowed by the child
    instead of terminating it.  ``start_new_session`` also keeps the group
    isolated from tmux's foreground process group.
    """
    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        try:
            signal.signal(getattr(signal, name), signal.SIG_DFL)
        except (AttributeError, OSError, ValueError):
            pass


def execute(role="", harness=None, model_target="", cwd=None, task="", cfg=None,
            timeout=None, request_id="", heartbeat_interval=None, on_event=None,
            cancel_event=None, cancel_callback=None,
            cancel_grace_seconds=CANCEL_GRACE_SECONDS,
            mode="READ_ONLY", workspace=None) -> dict:
    """Run ``task`` through ``harness`` and return the contract result.

    ``model_target`` is the already-resolved target; it is never resolved or
    substituted here. ``harness`` may be omitted when ``role`` already carries
    the harness key. ``cwd`` is the working directory (``None`` = inherit the
    caller's). ``timeout`` is an optional per-invocation cap in seconds.
    ``heartbeat_interval`` controls heartbeat cadence (default
    :data:`DEFAULT_HEARTBEAT_INTERVAL`). ``on_event``, when given, is called as
    ``on_event(kind, payload)`` with ``kind`` in ``{RUNNING, "HEARTBEAT",
    "OUTPUT"}``; an ``OUTPUT`` payload carries one rendered pane line in
    ``text`` (plain, no trailing newline) and its ``kind`` (one of
    :data:`FRAGMENT_KINDS`) so the terminal can colour it.
    ``cancel_event`` and ``cancel_callback`` are optional cooperative hooks
    used by the persistent terminal to stop this invocation on Ctrl+C.
    ``cfg`` is injectable for tests.
    ``mode`` controls workspace write-leasing (default ``READ_ONLY`` — no
    lease operations).  ``workspace`` overrides ``cwd`` as the lease scope
    when a write lease is acquired.

    The full ``task`` (any size, any number of embedded newlines) is delivered
    to the harness as exactly one subprocess argv element and produces
    exactly one harness invocation.
    """
    lease_workspace = workspace or (cwd or os.getcwd())
    lease = None
    lease_acquired = False
    try:
        from harness_allocator.lease import acquire as _lease_acquire, release as _lease_release
        if mode in ("WORKSPACE_WRITE", "FULL_ACCESS"):
            lease_acquired = True
            lease = _lease_acquire(
                workspace=lease_workspace,
                request_id=(request_id or "").strip() or make_request_id(),
                role=resolve_role_key(role),
                harness=(harness or "").strip() if harness else None,
                mode=mode,
            )
        start = _time.monotonic()
        if heartbeat_interval is None:
            heartbeat_interval = DEFAULT_HEARTBEAT_INTERVAL

        harness_key = (harness or "").strip() or resolve_harness(role)
        role_key = resolve_role_key(role)
        mt_identity = model_target_identity(model_target)
        rid = (request_id or "").strip() or make_request_id()
        ident = compute_identity(rid, task)

        base = {
            "request_id": rid,
            "harness": harness_key,
            "role": role_key,
            "model_target": mt_identity,
            "payload_chars": ident.chars,
            "payload_lines": ident.lines,
            "payload_sha256": ident.sha256,
        }

        try:
            argv = build_task_argv(harness_key, model_target=model_target, task=task, cfg=cfg)
            # Env wiring (D3 — Run 022 / HA-2 / D3 — Run 026 / HA-3 /
            # D3 — Run 027 / HA-4): the qwen child env is
            # {**parent_env, **build_qwen_env(...)}; the goose child env is
            # {**parent_env, **build_goose_env(...)}; the sweagent child env
            # ALWAYS carries the three SWE_AGENT_*_DIR values (load-bearing —
            # the bare CLI asserts on CONFIG_DIR.is_dir() — D1 / D3 contract);
            # the aider child env is the empty dict (aider inherits the parent
            # env — no load-bearing override exists for aider). All four share
            # the "empty dict -> env=None (inherit)" fallback so the existing
            # harness facade stays byte-identical.
            if harness_key == "qwen":
                qwen_env = build_qwen_env(model_target=model_target, cfg=cfg)
                env = {**os.environ, **qwen_env} if qwen_env else None
            elif harness_key == "goose":
                goose_env = build_goose_env(model_target=model_target, cfg=cfg)
                env = {**os.environ, **goose_env} if goose_env else None
            elif harness_key == "sweagent":
                # sweagent env is ALWAYS non-empty (the three SWE_AGENT_*_DIR
                # keys — adapter.build_sweagent_env never returns an empty
                # dict), so env threads unconditionally (no empty-dict fallback).
                sweagent_env = build_sweagent_env(model_target=model_target, cfg=cfg)
                env = {**os.environ, **sweagent_env}
            elif harness_key == "aider":
                # aider env returns {} (the empty-dict fallback — adapter
                # comment explains why: model is a CLI flag, key inherits).
                aider_env = build_aider_env(model_target=model_target, cfg=cfg)
                env = {**os.environ, **aider_env} if aider_env else None
            elif harness_key == "crush":
                # crush env is the qwen/goose empty-dict fallback
                # (adapter.build_crush_env returns {} when nothing is wired
                # — base_url empty AND api_key_env empty/named-var unset).
                # crush is a SUPPORTED chat-style harness (Run 028 / HA-5),
                # not experimental — no _require_experimental_enabled gate.
                crush_env = build_crush_env(model_target=model_target, cfg=cfg)
                env = {**os.environ, **crush_env} if crush_env else None
            else:
                env = None
            proc_result = run_argv(
                argv,
                cwd=cwd or os.getcwd(),
                timeout=timeout,
                heartbeat_interval=heartbeat_interval,
                on_event=on_event,
                event_context=dict(base),
                cancel_event=cancel_event,
                cancel_callback=cancel_callback,
                cancel_grace_seconds=cancel_grace_seconds,
                env=env,
            )
            return {**base, **proc_result}
        except Exception as exc:  # noqa: BLE001 — the contract reports, never raises
            elapsed = _time.monotonic() - start
            return {
                **base,
                "status": ERROR,
                "output": "",
                "error": str(exc),
                "elapsed": elapsed,
                "pid": None,
            }
    finally:
        if lease_acquired and lease is not None:
            _lease_release(lease)


class ProgressTracker:
    """What the harness is doing right now, read from its live stdout.

    A one-shot harness that emits JSONL events (simple-harness: started /
    model_request / assistant_stream / tool_call / tool_result / status /
    completed) tells us its phase line by line. The tracker keeps a phase
    — MODEL, STREAMING, TOOL, STATUS, OUTPUT or IDLE — a one-line activity
    and running counts, so a heartbeat can say "tool shell #12 running"
    instead of only "process_alive: true". Non-JSON output is counted, never
    parsed, so a harness with another output shape degrades to a line
    count rather than an error. Every method is safe to call from the
    drainer thread and the heartbeat loop concurrently: the state is a
    handful of scalars updated under one lock.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self.phase = "IDLE"
        self.activity = "waiting for output"
        self.requests = 0
        self.calls = 0
        self.errors = 0
        self.stream_chars = 0
        self.other_lines = 0
        self._tool_by_call = {}

    def feed(self, line):
        stripped = line.strip()
        if not stripped:
            return
        obj = None
        if stripped.startswith("{"):
            try:
                obj = json.loads(stripped)
            except ValueError:
                obj = None
        with self._lock:
            if not isinstance(obj, dict) or "event" not in obj:
                self.other_lines += 1
                self.phase = "OUTPUT"
                self.activity = f"{self.other_lines} output lines"
                return
            kind = obj.get("event")
            if kind == "model_request":
                self.requests += 1
                self.stream_chars = 0
                self.phase = "MODEL"
                self.activity = f"model request #{self.requests}"
            elif kind == "assistant_stream":
                self.stream_chars += len(str(obj.get("delta", "")))
                self.phase = "STREAMING"
                self.activity = f"reply #{self.requests} ({self.stream_chars} chars)"
            elif kind == "tool_call":
                self.calls += 1
                tool = obj.get("tool") or "?"
                self._tool_by_call[obj.get("call_id")] = tool
                self.phase = "TOOL"
                self.activity = f"tool {tool} #{self.calls} running"
            elif kind == "tool_result":
                tool = obj.get("tool") or self._tool_by_call.pop(obj.get("call_id"), "?")
                status = obj.get("tool_result_status") or "ok"
                if status != "ok":
                    self.errors += 1
                self.phase = "TOOL"
                self.activity = f"tool {tool} #{self.calls} {status}"
            elif kind == "status":
                status = str(obj.get("status", ""))
                if status != "STREAMING":
                    self.phase = "STATUS"
                    self.activity = status[:80]
            elif kind == "started":
                self.phase = "MODEL"
                self.activity = "session started"
            elif kind in ("completed", "interrupted"):
                self.phase = "STATUS"
                self.activity = kind

    def snapshot(self):
        with self._lock:
            return {
                "phase": self.phase,
                "activity": self.activity,
                "model_requests": self.requests,
                "tool_calls": self.calls,
                "tool_errors": self.errors,
            }


# ── rendering the event stream as pane text ─────────────────────────
#
# ONE renderer serves two readers: run_argv drives it line by line while the
# harness runs (fragments go out as OUTPUT events the moment they are
# complete), and terminal.render_child_output drives it over a finished
# buffer. Both produce the same lines, so the pane can never show one thing
# live and another after the fact.

_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"

#: Statuses the completion line already conveys; never shown as ``[status] …``.
_TERMINAL_STATUSES = ("COMPLETED", "FAILED", "INTERRUPTED")

#: Longest tool-error message shown on a pane line.
_TOOL_MESSAGE_MAX = 100

#: Kinds a pane fragment can carry; the terminal colours by kind, never by text.
FRAGMENT_KINDS = ("prose", "tool", "tool_error", "thinking", "status", "started", "passthrough")


class Fragment(tuple):
    """One pane line: ``(text, kind)``. Compares equal to a plain tuple."""

    __slots__ = ()

    def __new__(cls, text, kind="prose"):
        return tuple.__new__(cls, (text, kind))

    @property
    def text(self):
        return self[0]

    @property
    def kind(self):
        return self[1]


_GO_DURATION = re.compile(r"(\d+(?:\.\d+)?)(ns|us|µs|μs|ms|s|m|h)")
_GO_UNIT_SECONDS = {"ns": 1e-9, "us": 1e-6, "µs": 1e-6, "μs": 1e-6, "ms": 1e-3,
                    "s": 1.0, "m": 60.0, "h": 3600.0}


def parse_duration(value):
    """Seconds for a Go ``time.Duration.String()`` value, or ``None``.

    ``"2.021236ms"`` -> 0.002021236, ``"1m2.5s"`` -> 62.5. A number is taken
    as seconds. Anything else is ``None`` — the caller shows it verbatim.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return None
    pos = 0
    total = 0.0
    for match in _GO_DURATION.finditer(text):
        if match.start() != pos:
            return None
        total += float(match.group(1)) * _GO_UNIT_SECONDS[match.group(2)]
        pos = match.end()
    return total if pos == len(text) and pos > 0 else None


def format_duration(seconds):
    """``< 1 ms`` / ``2 ms`` / ``1.2 s`` — whole milliseconds, one decimal above a second."""
    ms = int(round(float(seconds) * 1000.0))
    if ms < 1:
        return "< 1 ms"
    if ms < 1000:
        return f"{ms} ms"
    return f"{ms / 1000.0:.1f} s"


def _held_tag_prefix(text, tag):
    """Length of the longest suffix of ``text`` that is a proper prefix of ``tag``.

    A tag can be split across two stream deltas (``"<thi"`` + ``"nk>"``);
    the suffix that might be the start of one is held back until the next
    delta decides what it was.
    """
    for n in range(min(len(tag) - 1, len(text)), 0, -1):
        if text.endswith(tag[:n]):
            return n
    return 0


def classify_tool_result(event, names):
    """What a ``tool_result`` event says, as a dict the renderer can coalesce.

    ``name`` is the tool; ``outcome`` is the text after the name that decides
    whether two consecutive results are the same thing (``ok``, ``exit=1``,
    ``ERROR path_escape``); ``detail`` is the message shown once after the
    outcome (``: start_line is not an int``); ``seconds`` is the parsed
    duration or ``None``; ``raw_duration`` is kept for an unparseable value;
    ``error`` says whether it counts as a tool error.
    """
    call_id = event.get("call_id")
    name = event.get("tool") or names.pop(call_id, None) or "?"
    content = event.get("content") or ""
    status = event.get("tool_result_status") or ""
    try:
        parsed = json.loads(content) if isinstance(content, str) else content
    except ValueError:
        parsed = None
    outcome = "ok"
    detail = ""
    seconds = None
    raw_duration = ""
    error = bool(status) and status != "ok"
    if isinstance(parsed, dict) and "kind" in parsed:
        error = True
        kind = str(parsed.get("kind"))
        message = " ".join(str(parsed.get("message", "")).split())
        outcome = f"ERROR {kind}"
        if kind == "path_escape":
            detail = " (absolute path rejected at the permission gate)"
        elif message:
            if len(message) > _TOOL_MESSAGE_MAX:
                message = message[:_TOOL_MESSAGE_MAX - 1] + "…"
            detail = f": {message}"
    elif error:
        outcome = status.upper()
    elif isinstance(parsed, dict) and "exit_code" in parsed:
        code = parsed.get("exit_code")
        outcome = "ok" if code == 0 else f"exit={code}"
    if isinstance(parsed, dict) and parsed.get("duration"):
        seconds = parse_duration(parsed.get("duration"))
        if seconds is None:
            raw_duration = str(parsed.get("duration"))
    return {"name": name, "outcome": outcome, "detail": detail,
            "seconds": seconds, "raw_duration": raw_duration, "error": error}


def render_tool_group(group):
    """The line for one or more coalesced tool results.

    ``[tool] shell ok (47 ms)`` for a single call; ``[tool] shell ×5 ok
    (max 2 ms)`` for a run of calls with the same tool and outcome;
    ``[tool] read_file ERROR path_escape (absolute path rejected at the
    permission gate)`` for an error, with ``×N`` after the name when repeated.
    """
    count = group["count"]
    line = f"[tool] {group['name']}"
    if count > 1:
        line += f" ×{count}"
    line += f" {group['outcome']}{group['detail']}"
    if group["max_seconds"] is not None:
        shown = format_duration(group["max_seconds"])
        line += f" (max {shown})" if count > 1 else f" ({shown})"
    elif group["raw_duration"]:
        line += f" ({group['raw_duration']})"
    return line


def render_tool_result(event, names):
    """One line for a single ``tool_result`` event (no coalescing)."""
    info = classify_tool_result(event, names)
    return render_tool_group({**info, "count": 1, "max_seconds": info["seconds"]})


def render_event(event, names):
    """The pane fragment for a non-prose, non-tool event, or ``None``.

    ``names`` maps call_id to tool name so a result whose event lacks the
    name can still be labelled; ``tool_call`` events register into it and
    render nothing. Prose (``assistant_stream``) is buffered and tool
    results are coalesced by :class:`LiveRenderer`; ``model_request`` is
    only counted; ``completed`` is carried by the terminal's completion
    line. ``status: STREAMING`` is noise, and the terminal statuses
    (COMPLETED / FAILED / INTERRUPTED) are what the completion line says.
    """
    kind = event.get("event")
    if kind in ("assistant_stream", "model_request", "tool_result", "completed", "usage"):
        # ``usage`` (simple-harness 2844dd2): token counts per model request;
        # totalled by LiveRenderer for the completion line, never a pane line.
        return None
    if kind == "tool_call":
        names[event.get("call_id")] = event.get("tool") or "?"
        return None
    if kind == "started":
        cfg = event.get("config") or {}
        return Fragment(
            f"[started] session {str(event.get('session_id', ''))[:8]} · "
            f"{cfg.get('model', '')} · {cfg.get('workspace', '')} · {cfg.get('permission', '')}",
            "started")
    if kind == "status":
        status = str(event.get("status", ""))
        if status == "STREAMING" or status in _TERMINAL_STATUSES:
            return None
        return Fragment(f"[status] {status}", "status")
    if kind == "interrupted":
        return Fragment("[interrupted]", "status")
    return Fragment(f"[{kind}]", "status")


class LiveRenderer:
    """Render a harness's JSONL event stream as pane text, as it arrives.

    ``feed(line)`` returns the fragments to print NOW — each one pane line
    as a :class:`Fragment` ``(text, kind)``, no trailing newline, no colour.
    Assistant deltas are buffered and released only as complete lines, so
    the pane reads like a reply being typed rather than a stutter of
    tokens. A ``<think>…</think>`` block is never shown: its text is
    suppressed while the block is open and replaced by a line
    ``[thinking: N chars]`` when it closes, or by
    ``[thinking, UNTERMINATED: N chars]`` if the prose ends inside it —
    that is the shape of a turn cut at the output ceiling (measured
    2026-09-01 on 9000-implementer: 33,630 characters, no closing tag,
    exit 0), and it must be visible as a defect rather than scroll past as
    prose.

    Consecutive tool results with the same tool and the same outcome are
    held and shown as ONE line (``[tool] shell ×5 ok (max 2 ms)``); the
    group is released by the next visible fragment of another kind, by
    :meth:`flush_tools` (the heartbeat calls it, so a long run of quiet
    tool calls still shows progress) or by :meth:`flush`. A non-JSON line
    passes through verbatim. Counts of model requests, tool calls and tool
    errors, the child's exit code and its last informative status feed the
    terminal's completion line.
    """

    def __init__(self):
        self._names = {}
        self._raw = ""            # delta text not yet classified (may end in a partial tag)
        self._line = ""           # visible text of the current, unterminated line
        self._in_think = False
        self._think_chars = 0
        self._block_started = False   # a visible line has gone out since the last flush
        self._pending_blanks = 0      # blank lines held until a non-blank one follows
        self._tools = None            # the pending coalesced tool group
        self.requests = 0
        self.calls = 0
        self.errors = 0
        self.out_tokens = 0
        self.reasoning_tokens = 0
        self.exit_code = None
        self.last_status = None       # last status that was neither STREAMING nor terminal

    def feed(self, line):
        """Absorb one raw stdout line; return the fragments to print now."""
        stripped = line.strip()
        if not stripped:
            return []
        obj = None
        if stripped.startswith("{"):
            try:
                obj = json.loads(stripped)
            except ValueError:
                obj = None
        if not isinstance(obj, dict) or "event" not in obj:
            return self.flush_tools() + [Fragment(line.rstrip("\r\n"), "passthrough")]
        return self.feed_event(obj)

    def feed_event(self, event):
        """Absorb one parsed event; return the fragments to print now."""
        kind = event.get("event")
        if kind == "usage":
            u = event.get("usage") or {}
            self.out_tokens += int(u.get("completion_tokens", 0) or 0)
            self.reasoning_tokens += int(u.get("reasoning_tokens", 0) or 0)
            return []
        if kind == "assistant_stream":
            out = self._absorb(str(event.get("delta", "")))
            return self.flush_tools() + out if out else out
        if kind == "model_request":
            self.requests += 1
            return []
        if kind == "tool_call":
            self.calls += 1
            self._names[event.get("call_id")] = event.get("tool") or "?"
            return []
        if kind == "tool_result":
            return self._absorb_tool(classify_tool_result(event, self._names))
        if kind == "completed":
            self.exit_code = event.get("exit_code", 0)
            return self.flush()
        if kind == "status":
            status = str(event.get("status", ""))
            if status == "STREAMING":
                return []   # noise between calls; must not break a tool group
            if status not in _TERMINAL_STATUSES:
                self.last_status = status
        out = self.flush()
        fragment = render_event(event, self._names)
        if fragment is not None:
            out.append(fragment)
        return out

    def flush(self):
        """Release everything held: the tool group first (it came first), then the prose."""
        prose = self._flush_prose()
        return self.flush_tools() + prose

    def _flush_prose(self):
        """End the prose block: release the held tail and the unterminated line."""
        if self._raw:
            if self._in_think:
                self._think_chars += len(self._raw)
            else:
                self._line += self._raw
            self._raw = ""
        if self._in_think:
            if self._line.strip():
                self._line += "\n"
            else:
                self._line = ""
            self._line += f"[thinking, UNTERMINATED: {self._think_chars} chars]"
            self._in_think = False
            self._think_chars = 0
        out = self._release_lines()
        if self._line.strip():
            self._emit(self._line, out)
        self._line = ""
        self._block_started = False
        self._pending_blanks = 0
        return out

    def flush_tools(self):
        """Release the pending tool group as its one line, if there is one."""
        if self._tools is None:
            return []
        group, self._tools = self._tools, None
        return [Fragment(render_tool_group(group), "tool_error" if group["error"] else "tool")]

    def counts(self):
        return {"model_requests": self.requests, "tool_calls": self.calls,
                "tool_errors": self.errors}

    def tail(self):
        """``11 req / 17 calls / 3 err`` — the counts as the completion line shows them."""
        base = f"{self.requests} req / {self.calls} calls / {self.errors} err"
        if self.out_tokens:
            base += f" / {_fmt_tokens(self.out_tokens)} out ({_fmt_tokens(self.reasoning_tokens)} think)"
        return base

    def summary(self):
        return (f"[turns] {self.requests} model requests, {self.calls} tool calls, "
                f"{self.errors} tool errors")

    # ── tool coalescing ──

    def _absorb_tool(self, info):
        if info["error"]:
            self.errors += 1
        key = (info["name"], info["outcome"])
        # A tool result ends the model's turn: an unterminated prose line is
        # released now, after any group it followed.
        prose = self._flush_prose()
        out = self.flush_tools() + prose if prose else []
        if self._tools is not None and self._tools["key"] == key:
            group = self._tools
            group["count"] += 1
            if info["seconds"] is not None:
                group["max_seconds"] = (info["seconds"] if group["max_seconds"] is None
                                        else max(group["max_seconds"], info["seconds"]))
            elif info["raw_duration"] and not group["raw_duration"]:
                group["raw_duration"] = info["raw_duration"]
            return out
        out += self.flush_tools()
        self._tools = {**info, "key": key, "count": 1, "max_seconds": info["seconds"]}
        return out

    # ── prose buffering ──

    def _absorb(self, delta):
        self._raw += delta
        while self._raw:
            if self._in_think:
                idx = self._raw.find(_THINK_CLOSE)
                if idx == -1:
                    keep = _held_tag_prefix(self._raw, _THINK_CLOSE)
                    self._think_chars += len(self._raw) - keep
                    self._raw = self._raw[len(self._raw) - keep:]
                    break
                self._think_chars += idx
                # The marker takes a line of its own; text already on the
                # current line is closed first, whitespace alone is dropped.
                if self._line.strip():
                    self._line += "\n"
                else:
                    self._line = ""
                self._line += f"[thinking: {self._think_chars} chars]\n"
                self._in_think = False
                self._raw = self._raw[idx + len(_THINK_CLOSE):]
            else:
                idx = self._raw.find(_THINK_OPEN)
                if idx == -1:
                    keep = _held_tag_prefix(self._raw, _THINK_OPEN)
                    self._line += self._raw[:len(self._raw) - keep]
                    self._raw = self._raw[len(self._raw) - keep:]
                    break
                self._line += self._raw[:idx]
                self._in_think = True
                self._think_chars = 0
                self._raw = self._raw[idx + len(_THINK_OPEN):]
        return self._release_lines()

    def _release_lines(self):
        out = []
        while "\n" in self._line:
            head, self._line = self._line.split("\n", 1)
            self._emit(head, out)
        return out

    def _emit(self, text, out):
        # A block is shown the way a finished one was: no leading blank
        # lines or indent, no trailing blank lines, interior blanks kept.
        text = text.rstrip()
        if not self._block_started:
            text = text.lstrip()
            if not text:
                return
        elif not text:
            self._pending_blanks += 1
            return
        out.extend([Fragment("", "prose")] * self._pending_blanks)
        self._pending_blanks = 0
        if text.startswith("[thinking") and text.endswith(" chars]"):
            # The marker stands alone; the blank lines a model emits right
            # after </think> are leading blanks of the prose that follows.
            out.append(Fragment(text, "thinking"))
            return
        self._block_started = True
        out.append(Fragment(text, "prose"))


def run_argv(argv, *, cwd, timeout=None, heartbeat_interval=15.0,
             on_event=None, event_context=None, cancel_event=None,
             cancel_callback=None,
             cancel_grace_seconds=CANCEL_GRACE_SECONDS,
             env=None) -> dict:
    """Spawn ``argv`` and return ``{status, output, error, elapsed, pid}``.

    The argv list is passed directly to :class:`subprocess.Popen` — no shell,
    no shlex round-trip — so the complete prompt is delivered as one argv
    element regardless of embedded newlines or shell metacharacters.

    Emits ``RUNNING`` (with pid) at start and ``HEARTBEAT`` (with elapsed and
    process-alive) while the subprocess stays alive, through ``on_event``.
    Output pipes are drained on a background thread so a large payload/output
    can never deadlock the heartbeat loop. Each drained stdout line is also
    rendered by :class:`LiveRenderer` and every finished fragment goes out
    at once as ``on_event("OUTPUT", {..., "text": plain, "kind": kind})`` —
    from the drainer thread, so the callback must tolerate that. A heartbeat
    is then emitted only after a full interval in which nothing was printed:
    its job is to show life during silence, not to interleave with a reply.
    Before it fires it releases a tool group the renderer is still holding,
    so a long run of identical tool calls shows as ``×N`` progress instead
    of nothing. The result carries the child's ``exit_code``.
    """
    ctx = dict(event_context or {})
    argv = list(argv)
    if _cancellation_requested(cancel_event, cancel_callback):
        return {
            "status": CANCELLED,
            "output": "",
            "error": "cancelled by Ctrl+C",
            "elapsed": 0.0,
            "pid": None,
            "cancelled": True,
        }

    popen_kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "cwd": cwd,
    }
    if env is not None:
        popen_kwargs["env"] = env
    if os.name == "posix":
        popen_kwargs.update({
            "start_new_session": True,
            "preexec_fn": _reset_child_signal_handlers,
        })
    try:
        proc = subprocess.Popen(argv, **popen_kwargs)
    except Exception as exc:  # noqa: BLE001 — reported, never raised
        if on_event:
            on_event(ERROR, {**ctx, "pid": None, "elapsed": 0.0})
        return {
            "status": ERROR,
            "output": "",
            "error": str(exc),
            "elapsed": 0.0,
            "pid": None,
        }

    start = _time.monotonic()
    pid = proc.pid
    if on_event:
        on_event(RUNNING, {**ctx, "pid": pid, "elapsed": 0.0, "process_alive": True})

    captured = {}
    progress = ProgressTracker()
    renderer = LiveRenderer() if on_event else None
    # last_output_at is read by the heartbeat loop and written by the drainer;
    # a single float assignment, so no lock is needed. "failed" stops live
    # emission after the callback raised once — the run's output must never
    # be lost to a display problem, so the drainer keeps draining.
    live = {"last_output_at": start, "failed": ""}
    # The renderer is fed by the drainer and flushed by the heartbeat loop.
    render_lock = threading.Lock()

    def _emit_live(fragments):
        if not fragments or live["failed"]:
            return
        try:
            for text, kind in fragments:
                on_event("OUTPUT", {**ctx, "pid": pid, "text": text, "kind": kind})
        except Exception as exc:  # noqa: BLE001 — reported in the result, never raised here
            live["failed"] = f"live render failed: {exc!r}"
            return
        live["last_output_at"] = _time.monotonic()

    def _capture():
        # Read stdout LINE BY LINE so progress is visible while the harness
        # runs; communicate() would hand us everything only at exit, which is
        # exactly when a heartbeat no longer needs it. stderr is drained on
        # its own thread so a chatty child can never block on a full pipe.
        # A process object without line-readable pipes (the test fakes, a
        # caller that pre-wired communicate()) keeps the historical path.
        stdout = getattr(proc, "stdout", None)
        stderr = getattr(proc, "stderr", None)
        if stdout is None or not hasattr(stdout, "readline"):
            out, err = proc.communicate()
            captured["out"] = out or ""
            captured["err"] = err or ""
            return
        err_box = {"err": ""}

        def _drain_err():
            try:
                err_box["err"] = stderr.read() or "" if stderr is not None else ""
            except (OSError, ValueError):
                err_box["err"] = ""
        err_thread = threading.Thread(target=_drain_err, daemon=True)
        err_thread.start()
        lines = []
        try:
            for line in iter(stdout.readline, ""):
                lines.append(line)
                progress.feed(line)
                if renderer is not None:
                    with render_lock:
                        _emit_live(renderer.feed(line))
        except (OSError, ValueError):
            pass
        if renderer is not None:
            with render_lock:
                _emit_live(renderer.flush())
        err_thread.join()
        try:
            proc.wait()
        except Exception:  # noqa: BLE001 — the poll loop owns the exit code
            pass
        captured["out"] = "".join(lines)
        captured["err"] = err_box["err"]

    drainer = threading.Thread(target=_capture, daemon=True)
    drainer.start()

    deadline = (start + timeout) if timeout else None
    last_hb = start
    cancel_seen = False
    cancel_started_at = None
    cancel_stage = 0
    timed_out = False
    try:
        grace = max(0.0, float(cancel_grace_seconds))
    except (TypeError, ValueError):
        grace = CANCEL_GRACE_SECONDS
    term_at = grace * _CANCEL_TERM_ESCALATION_FRACTION
    while True:
        rc = proc.poll()
        if rc is not None:
            break
        now = _time.monotonic()
        if _cancellation_requested(cancel_event, cancel_callback):
            cancel_seen = True
        if not cancel_seen and deadline is not None and now >= deadline:
            _signal_process_group(proc, signal.SIGKILL)
            timed_out = True
            break
        if cancel_seen and cancel_stage == 0:
            cancel_started_at = now
            cancel_stage = 1
            _signal_process_group(proc, signal.SIGINT)
        if cancel_seen and cancel_stage == 1 and now - cancel_started_at >= term_at:
            cancel_stage = 2
            _signal_process_group(proc, signal.SIGTERM)
        if cancel_seen and cancel_stage == 2 and now - cancel_started_at >= grace:
            cancel_stage = 3
            _signal_process_group(proc, signal.SIGKILL)
        # Silence is measured from whichever spoke last — the previous
        # heartbeat or the last live fragment — so a streaming reply is
        # never interrupted by one, and a quiet stretch still gets one.
        if now - max(last_hb, live["last_output_at"]) >= heartbeat_interval:
            if renderer is not None:
                with render_lock:
                    _emit_live(renderer.flush_tools())
            if on_event:
                on_event(
                    "HEARTBEAT",
                    {**ctx, "pid": pid, "elapsed": now - start, "process_alive": True,
                     **progress.snapshot()},
                )
            last_hb = now
        _time.sleep(0.2)

    # The drainer normally finishes with the leader.  If a descendant kept a
    # pipe open after the leader was killed, wait through the bounded grace
    # window, then force the same isolated group once more.
    drainer.join(timeout=grace + 0.25)
    if drainer.is_alive():
        _signal_process_group(proc, signal.SIGKILL)
        drainer.join(timeout=0.5)
    if hasattr(proc, "wait") and proc.poll() is not None:
        try:
            proc.wait(timeout=0.1)
        except TypeError:
            pass
        except Exception:
            pass
    elapsed = _time.monotonic() - start
    rc = proc.returncode
    if cancel_seen:
        status = CANCELLED
    elif timed_out:
        status = ERROR
    else:
        status = SUCCESS if rc == 0 else ERROR
    err = captured.get("err", "")
    if timed_out and not err:
        err = "harness invocation timed out"
    if status == CANCELLED and not err:
        err = "cancelled by Ctrl+C"
    if live["failed"]:
        err = f"{err}\n{live['failed']}" if err else live["failed"]
    return {
        "status": status,
        "output": captured.get("out", ""),
        "error": err,
        "elapsed": elapsed,
        "pid": pid,
        "exit_code": rc,
        "cancelled": cancel_seen,
        "process_group": pid if os.name == "posix" else None,
    }


def run_command(command, *, cwd, timeout=None, heartbeat_interval=15.0,
                on_event=None, event_context=None, cancel_event=None,
                cancel_callback=None,
                cancel_grace_seconds=CANCEL_GRACE_SECONDS) -> dict:
    """Spawn ``command`` and return ``{status, output, error, elapsed, pid}``.

    Accepts either a shell command string (split with :func:`shlex.split`) or
    an argv list (used as-is, no shell). Prefer passing an argv list for any
    task containing embedded newlines, quotes, or shell metacharacters; the
    argv path is what :func:`execute` itself uses internally.
    """
    if isinstance(command, str):
        argv = shlex.split(command)
    else:
        argv = list(command)
    return run_argv(
        argv,
        cwd=cwd,
        timeout=timeout,
        heartbeat_interval=heartbeat_interval,
        on_event=on_event,
        event_context=event_context,
        cancel_event=cancel_event,
        cancel_callback=cancel_callback,
        cancel_grace_seconds=cancel_grace_seconds,
    )



def execute_spec(spec, cfg=None, mode="READ_ONLY", workspace=None):
    """Run ``spec`` through ``execute``'s existing adapter path and return a
    :class:`HarnessRunResult`.

    This is the RunSpec-based entry ALONGSIDE the frozen ``execute`` facade
    (GOAL.md Run 020 §1 D3). ``execute``'s signature and emitted argv are
    byte-identical to pristine master ``c202286`` — this function is
    ADDITIVE only.

    Behavior (BOUND):

    1. ``spec.validate()`` runs first. The three D1 typed errors propagate
       (UnknownHarnessError, MissingPromptError, UnsupportedCapabilityError).
       All three are raised BEFORE any subprocess — the typed refusal
       contract (TG7 / D4).

    2. The one-shot argv is built via the EXISTING adapter path
       ``build_task_argv(spec.harness, model_target=spec.model_reference,
       task=spec.prompt, cfg=cfg)``. ``build_task_argv`` raises ``ValueError``
       ("no one-shot task invocation for harness '...'") for the resident
       TUIs (``codex`` / ``claude-code`` / ``opencode``); that ValueError
       is caught and re-raised as ``LargeInputRefusedError`` (naming the
       harness) — the typed non-interactive / large-paste refusal. It too
       MUST happen BEFORE any subprocess.

    3. The argv is run via the EXISTING ``run_argv`` path. There is NO
       tempfile / stdin mechanism in the package — large input routes
       through the single-argv-element delivery that ``build_task_argv``
       produces.

    4. A :class:`HarnessRunResult` is filled and returned:
       ``status = proc["status"]``; ``exit_code = 0`` on SUCCESS, ``None``
       otherwise (BOUND — ``run_argv`` does not expose the child return
       code, so we MUST NOT fabricate a nonzero value); ``stdout`` /
       ``stderr`` map from ``proc["output"]`` / ``proc["error"]``;
       ``request_id`` is ``spec.request_id`` if set else a fresh
       ``make_request_id()``; ``session_id`` is the spec's session id or
       ``None`` (the one-shot path exposes none); ``artifacts`` is the
       spec's expected_artifacts list; ``evidence`` and ``usage`` are
       fresh empty / None instances (the one-shot path tracks nothing —
       "nulls where a harness exposes nothing"); ``timing.started_at`` /
       ``timing.finished_at`` are float POSIX seconds captured via
       :func:`time.time` around the ``run_argv`` call.

     Stdlib only — no new third-party imports.
     """
    lease = None
    try:
        from harness_allocator.lease import acquire as _lease_acquire, release as _lease_release

        # (1) Validate first; typed errors propagate BEFORE any subprocess.
        spec.validate()

        harness_key = (spec.harness or "").strip()
        lease_workspace = workspace or (spec.working_directory or os.getcwd())
        if mode in ("WORKSPACE_WRITE", "FULL_ACCESS"):
            lease = _lease_acquire(
                workspace=lease_workspace,
                request_id=(spec.request_id or "").strip() or make_request_id(),
                role=resolve_role_key(spec.role) if hasattr(spec, 'role') and spec.role else "unknown",
                harness=harness_key,
                mode=mode,
            )

        # (2) Build the one-shot argv via the EXISTING adapter path. Resident
        #     TUIs (codex/claude-code/opencode) raise ValueError from
        #     build_task_argv; we catch and re-raise as LargeInputRefusedError
        #     (the typed non-interactive / large-paste refusal — TG7 / D4).
        try:
            argv = build_task_argv(
                harness_key,
                model_target=spec.model_reference,
                task=spec.prompt,
                cfg=cfg,
            )
        except ValueError as exc:
            # Resident TUIs have no one-shot form; refuse with the typed error.
            if "no one-shot task invocation" in str(exc):
                raise LargeInputRefusedError(
                    f"harness {harness_key!r} has no non-interactive one-shot "
                    f"form; large-paste input is refused for resident TUIs "
                    f"({exc})"
                ) from exc
            raise

        # (3) Run it via the existing run_argv path. capture wall-clock time
        #     around the call for timing.started_at/finished_at. The qwen
        #     child env threads {**parent_env, **build_qwen_env(...)}; the
        #     goose child env threads {**parent_env, **build_goose_env(...)};
        #     the sweagent child env ALWAYS threads the three SWE_AGENT_*_DIR
        #     values; the aider child env falls through to None (aider
        #     inherits the parent env). Non-qwen / non-goose / non-sweagent /
        #     non-aider specs pass env=None (inherit) and stay byte-identical
        #     to the existing four-harness facade
        #     (D3 — Run 022 / HA-2; D3 — Run 026 / HA-3; D3 — Run 027 / HA-4).
        cwd = spec.working_directory or os.getcwd()
        if harness_key == "qwen":
            qwen_env = build_qwen_env(model_target=spec.model_reference, cfg=cfg)
            env = {**os.environ, **qwen_env} if qwen_env else None
        elif harness_key == "goose":
            goose_env = build_goose_env(model_target=spec.model_reference, cfg=cfg)
            env = {**os.environ, **goose_env} if goose_env else None
        elif harness_key == "sweagent":
            # sweagent env is ALWAYS non-empty (the three SWE_AGENT_*_DIR
            # keys — adapter.build_sweagent_env never returns an empty dict).
            sweagent_env = build_sweagent_env(model_target=spec.model_reference, cfg=cfg)
            env = {**os.environ, **sweagent_env}
        elif harness_key == "aider":
            # aider env returns {} (empty-dict fallback — adapter comment
            # explains: model is a CLI flag, key inherits).
            aider_env = build_aider_env(model_target=spec.model_reference, cfg=cfg)
            env = {**os.environ, **aider_env} if aider_env else None
        elif harness_key == "crush":
            # crush env threads {**parent_env, **build_crush_env(...)} with
            # the qwen/goose empty-dict fallback (Run 028 / HA-5 — chat-style
            # supported harness, base_url configured via crushrc not env,
            # OPENAI_API_KEY read from a NAMED env var).
            crush_env = build_crush_env(model_target=spec.model_reference, cfg=cfg)
            env = {**os.environ, **crush_env} if crush_env else None
        else:
            env = None
        started_at = _time.time()
        proc = run_argv(
            argv,
            cwd=cwd,
            timeout=spec.timeout,
            env=env,
        )
        finished_at = _time.time()

        # (4) Fill and return HarnessRunResult with the BOUND exit_code
        #     semantics: 0 on SUCCESS, None otherwise. run_argv does not
        #     expose the child return code, so we MUST NOT fabricate a real
        #     nonzero value.
        status = proc.get("status", ERROR)
        exit_code = 0 if status == SUCCESS else None

        return HarnessRunResult(
            request_id=(spec.request_id or "").strip() or make_request_id(),
            harness=harness_key,
            status=status,
            exit_code=exit_code,
            session_id=(spec.session.session_id or "").strip() or None,
            stdout=proc.get("output", ""),
            stderr=proc.get("error", ""),
            artifacts=list(spec.output.expected_artifacts),
            evidence=RunEvidence(),
            usage=RunUsage(),
            timing=RunTiming(started_at=started_at, finished_at=finished_at),
        )
    finally:
        if lease is not None:
            _lease_release(lease)


def _fmt_tokens(n):
    """``7.1k`` above a thousand, the bare number below."""
    return f"{n/1000:.1f}k" if n >= 1000 else str(n)
