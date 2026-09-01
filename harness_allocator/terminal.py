"""HarnessTerminal — a persistent, harness-neutral terminal for one-shot harnesses.

Runs inside a role's tmux session. It prints a role/harness/model banner, then
loops: read one atomic framed request from stdin, execute it through the
resolved harness (one-shot), show operational progress, and return to READY.

The terminal's input can be either length-delimited frames (``transport``) or,
if the caller prefers, a line-based reader that delivers one whole task per
read. Embedded newlines in a delivered task are content, never request
boundaries: ONE complete semantic task = EXACTLY ONE harness invocation.

Progress is operational metadata only (request id, pid, elapsed,
process-alive) — never chain-of-thought.

Harness-neutral by construction: it knows only a resolved harness key plus a
role/harness/model-target identity — never any flow, verdict, sequencing or
governance semantics. The caller composes the task and sends it here; this
process only runs it through the allocator's ``execute``.

Duplicate-request protection: once a request completes, its
``(request_id, payload_sha256)`` identity is recorded for this terminal
session. A later frame with the same completed identity is NOT executed again;
it reports ``[DUPLICATE_REQUEST]`` and returns to READY. The only way to
re-run a completed identity is an explicit ``retry`` flag on the frame
(``encode_request(..., retry=True)``), which re-executes and re-records it.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import signal
import sys
import threading

from .definition import model_target_identity
from .invoke import execute
from .status import (
    CANCELLED,
    CLEANUP,
    DUPLICATE_REQUEST,
    ERROR,
    INTERRUPTING,
    NOT_CONFIGURED,
    READY,
    RUNNING,
    SUCCESS,
    UNKNOWN,
    status_value,
)
from .transport import FrameReader, compute_identity

#: Human labels for known harnesses — display only, never routing.
HARNESS_LABELS = {
    "dsh": "DeepSeek Harness",
    "codex": "Codex",
    "claude-code": "Claude Code",
    "opencode": "OpenCode",
}

#: Human labels for known model targets — display only, never routing.
MODEL_LABELS = {
    "deepseek-v4-pro": "DeepSeek V4 Pro",
    "MiniMax-M3": "MiniMax M3",
    "sonnet5": "Claude Sonnet 5",
}

MCP_LIGHT_STATES = ("connected", "available", "unavailable", "not configured")
_FRAME_INTERRUPTED = object()


def _label(labels, key):
    return labels.get(key, key)


def _fmt_elapsed(seconds):
    return f"{float(seconds):.2f}s"


def _runtime_info(status_info=None, runtime_info=None):
    source = status_info if status_info is not None else runtime_info
    return dict(source or {})


def render_banner(role, harness_key, model_target, cwd, flow="", status_info=None,
                  runtime_info=None):
    """The startup identity block, before the first READY prompt."""
    info = _runtime_info(status_info, runtime_info)
    if not flow:
        flow = status_value(info, "flow", "")
    lines = [
        "Harness Allocator Terminal",
        "",
    ]
    if flow:
        lines.append(f"Flow:    {flow}")
    lines += [
        f"Role:    {role}",
        f"Harness: {_label(HARNESS_LABELS, harness_key)}",
        f"Model target: {_label(MODEL_LABELS, model_target_identity(model_target))}",
        f"Mode:    headless / one-shot",
        f"Cwd:     {cwd}",
    ]
    lines += [
        "",
        f"Sandbox: {status_value(info, 'sandbox_mode', UNKNOWN, ('workspace-write', 'full-access', 'read-only', UNKNOWN))}",
        f"Approval: {status_value(info, 'approval_policy', UNKNOWN, ('never', 'on-request', 'untrusted', UNKNOWN))}",
        f"Workspace: {status_value(info, 'workspace_access_mode', UNKNOWN, ('writable', 'read-only', UNKNOWN))}",
        f"Bridge/flows: {status_value(info, 'bridge_dir', NOT_CONFIGURED)} "
        f"({status_value(info, 'bridge_dir_access', UNKNOWN, ('writable', 'read-only', UNKNOWN))})",
        f"MCP-Light: {status_value(info, 'mcp_light', NOT_CONFIGURED, MCP_LIGHT_STATES)}",
        f"Permission: {status_value(info, 'permission', UNKNOWN, ('read-only', 'workspace-write', 'full-access', UNKNOWN))}",
        f"Request ID: {status_value(info, 'request_id', UNKNOWN)}",
    ]
    return "\n".join(lines)


def _ready_line(role):
    return f"\nStatus: {READY}\n\n{role}> "


# ── child output rendering ─────────────────────────────────────────


_THINK_RE = None


def _collapse_thinking(text):
    """Replace <think>…</think> blocks with a one-line marker.

    A reasoning block is the bulk of a chat-model turn and none of it is
    the deliverable; the pane needs to know it happened and how big it was,
    not read it. An unterminated block is marked as such — that is the
    shape of a turn cut at the output ceiling (measured 2026-09-01 on
    9000-implementer: 33,630 characters, no closing tag, exit 0), and it
    must be visible as a defect rather than scroll past as prose.
    """
    global _THINK_RE
    if _THINK_RE is None:
        import re
        _THINK_RE = re.compile(r"<think>(.*?)</think>", re.S)
    text = _THINK_RE.sub(lambda m: f"[thinking: {len(m.group(1))} chars]", text)
    open_at = text.rfind("<think>")
    if open_at != -1:
        body = text[open_at + len("<think>"):]
        text = text[:open_at] + f"[thinking, UNTERMINATED: {len(body)} chars]"
    return text


def _render_tool_result(event, names):
    call_id = event.get("call_id")
    name = event.get("tool") or names.pop(call_id, None) or "?"
    content = event.get("content") or ""
    status = event.get("tool_result_status") or ""
    detail = ""
    try:
        parsed = json.loads(content) if isinstance(content, str) else content
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        if "kind" in parsed:
            detail = f"{parsed.get('kind')}: {str(parsed.get('message', ''))[:160]}"
            status = status or "error"
        elif "exit_code" in parsed:
            detail = f"exit={parsed.get('exit_code')}"
            dur = parsed.get("duration")
            if dur:
                detail += f" ({dur})"
    line = f"[tool] {name}"
    if status and status != "ok":
        line += f" {status.upper()}"
    if detail:
        line += f" {detail}"
    return line


def render_child_output(out):
    """Render a harness's JSONL event stream as readable text.

    The one-shot harness is launched with ``--output jsonl`` so the runner
    can read status and exit code by machine; the pane, though, is read by
    a person. Every line that is a JSON object carrying an ``event`` key is
    rendered: assistant deltas are joined into prose (reasoning collapsed),
    each tool result becomes one line, status changes and the completion
    are kept, and a summary counts model requests, tool calls and tool
    errors. If ANY non-empty line is not such an object the whole output
    is returned verbatim — a harness that does not speak this protocol, or
    a mixed stream, is shown exactly as it was, never half-rendered. The
    full event stream stays in the harness's own session log on disk.
    """
    events = []
    for line in out.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except ValueError:
            return out
        if not isinstance(obj, dict) or "event" not in obj:
            return out
        events.append(obj)
    if not events:
        return out

    rendered = []
    text = []
    names = {}
    requests = calls = errors = 0

    def flush_text():
        if not text:
            return
        block = _collapse_thinking("".join(text)).strip()
        text.clear()
        if block:
            rendered.append(block)

    for event in events:
        kind = event.get("event")
        if kind == "assistant_stream":
            text.append(str(event.get("delta", "")))
            continue
        if kind == "model_request":
            requests += 1
            continue
        if kind == "tool_call":
            calls += 1
            names[event.get("call_id")] = event.get("tool") or "?"
            continue
        flush_text()
        if kind == "started":
            cfg = event.get("config") or {}
            rendered.append(
                f"[started] session {str(event.get('session_id', ''))[:8]} "
                f"model={cfg.get('model', '')} workspace={cfg.get('workspace', '')} "
                f"permission={cfg.get('permission', '')}"
            )
        elif kind == "tool_result":
            line = _render_tool_result(event, names)
            if "ERROR" in line or "FAILED" in line:
                errors += 1
            rendered.append(line)
        elif kind == "status":
            status = str(event.get("status", ""))
            if status != "STREAMING":
                rendered.append(f"[status] {status}")
        elif kind == "completed":
            rendered.append(f"[completed] exit={event.get('exit_code', 0)}")
        elif kind == "interrupted":
            rendered.append("[interrupted]")
        else:
            rendered.append(f"[{kind}]")
    flush_text()
    rendered.append(
        f"[turns] {requests} model requests, {calls} tool calls, {errors} tool errors"
    )
    return "\n".join(rendered)


def run_terminal(*, role, harness, model_target, cwd, flow="", reader, writer,
                 runner=None, heartbeat_interval=15.0, timeout=None,
                 status_info=None, runtime_info=None, cancel_event=None,
                 cancel_callback=None) -> int:
    """Run the persistent READY -> execute -> READY loop.

    The optional ``cancel_event`` is shared with the active runner.  A SIGINT
    received while READY is consumed by this loop: the pending input is
    discarded, a short deterministic notice is written, and the prompt remains
    READY.  A SIGINT while RUNNING sets the event so the runner can terminate
    only the child process group it owns.
    """
    if runner is None:
        runner = execute

    if cancel_event is not None:
        cancel_event.clear()
    ready_interrupt = threading.Event()
    active_runner = {"value": False}
    previous_sigint = None

    def _handle_sigint(_signum, _frame):
        if active_runner["value"] and cancel_event is not None:
            cancel_event.set()
        else:
            ready_interrupt.set()

    # Signal handlers belong to the main thread in a persistent terminal.  A
    # caller embedding run_terminal in a non-main thread may provide its own
    # cancel hook instead; the ready-side state handling remains available.
    try:
        previous_sigint = signal.signal(signal.SIGINT, _handle_sigint)
    except (ValueError, OSError):
        previous_sigint = None

    info = _runtime_info(status_info, runtime_info)
    writer.write(render_banner(role, harness, model_target, cwd, flow, status_info=info))
    writer.write(_ready_line(role))
    writer.flush()

    completed = set()
    harness_label = _label(HARNESS_LABELS, harness)
    model_label = _label(MODEL_LABELS, model_target_identity(model_target))

    def _read_frame():
        try:
            return reader.read_frame()
        except InterruptedError:
            return _FRAME_INTERRUPTED
        except OSError as exc:
            if getattr(exc, "errno", None) == errno.EINTR:
                return _FRAME_INTERRUPTED
            raise

    def _show_ready_after_interrupt():
        ready_interrupt.clear()
        clear_reader = getattr(reader, "clear", None) or getattr(reader, "clear_buffer", None)
        if callable(clear_reader):
            try:
                clear_reader()
            except Exception:
                pass
        writer.write("\n[READY] Ctrl+C ignored; terminal remains READY.\n")
        writer.write(_ready_line(role))
        writer.flush()

    def on_event(kind, payload):
        if kind == RUNNING:
            writer.write(f"\n[{RUNNING}] {harness_label} / {model_label}\n")
            writer.write(f"pid: {payload.get('pid')}\n")
            writer.write(f"elapsed: {_fmt_elapsed(payload.get('elapsed', 0.0))}\n")
        elif kind == "HEARTBEAT":
            # One line. The phase and activity come from the runner's live
            # read of the harness's event stream (invoke.ProgressTracker), so
            # the pane says what the role is doing — a tool call, a model
            # request, a reply streaming — not only that the process lives.
            phase = payload.get("phase")
            head = f"[HEARTBEAT - {phase}]" if phase else "[HEARTBEAT]"
            parts = [head, str(payload.get("request_id")),
                     _fmt_elapsed(payload.get("elapsed", 0.0))]
            if payload.get("activity"):
                parts.append(payload["activity"])
                parts.append(f"{payload.get('model_requests', 0)} req / "
                             f"{payload.get('tool_calls', 0)} calls / "
                             f"{payload.get('tool_errors', 0)} err")
            else:
                parts.append("alive" if payload.get("process_alive", True) else "not alive")
            writer.write(" · ".join(parts) + "\n")
        writer.flush()

    try:
        while True:
            if ready_interrupt.is_set():
                _show_ready_after_interrupt()
                continue
            frame = _read_frame()
            if ready_interrupt.is_set() or frame is _FRAME_INTERRUPTED:
                _show_ready_after_interrupt()
                continue
            if frame is None:
                # EOF: the tmux session was closed — clean shutdown.
                break

            task = frame.payload
            if not task.strip():
                writer.write(f"{role}> ")
                writer.flush()
                continue

            ident = compute_identity(frame.request_id, task)
            key = (ident.request_id, ident.sha256)
            if key in completed and not frame.retry:
                writer.write("\n[DUPLICATE_REQUEST]\n")
                writer.write(f"request_id: {ident.request_id}\n")
                writer.write(f"sha256: {ident.sha256}\n")
                writer.write(_ready_line(role))
                writer.flush()
                continue

            writer.write("\n[DISPATCH]\n")
            writer.write(f"request_id: {ident.request_id}\n")
            writer.write(f"chars: {ident.chars}\n")
            writer.write(f"lines: {ident.lines}\n")
            writer.write(f"sha256: {ident.sha256}\n")
            writer.write(f"harness: {harness_label}\n")
            writer.write(f"role: {role}\n")
            writer.write(f"model_target: {model_label}\n")
            if frame.retry:
                writer.write("retry: true\n")
            writer.flush()

            runner_kwargs = {
                "role": role,
                "harness": harness,
                "model_target": model_target,
                "cwd": cwd,
                "task": task,
                "request_id": frame.request_id,
                "heartbeat_interval": heartbeat_interval,
                "timeout": timeout,
                "on_event": on_event,
            }
            if cancel_event is not None:
                runner_kwargs["cancel_event"] = cancel_event
            if cancel_callback is not None:
                runner_kwargs["cancel_callback"] = cancel_callback
            active_runner["value"] = True
            try:
                result = runner(**runner_kwargs)
            except KeyboardInterrupt:
                if cancel_event is not None and cancel_event.is_set():
                    result = {
                        "status": CANCELLED,
                        "output": "",
                        "error": "cancelled by Ctrl+C",
                        "elapsed": 0.0,
                        "pid": None,
                    }
                else:
                    raise
            finally:
                active_runner["value"] = False

            if cancel_event is not None:
                cancel_event.clear()
            completed.add(key)
            status = result["status"]
            writer.write(f"\n[{status}]\n")
            writer.write(f"request_id: {frame.request_id}\n")
            writer.write(f"duration: {_fmt_elapsed(result.get('elapsed', 0.0))}\n")
            out = (result.get("output") or "").strip()
            err = (result.get("error") or "").strip()
            if out:
                writer.write(render_child_output(out) + "\n")
            if err:
                writer.write(f"[stderr] {err}\n")
            writer.write(_ready_line(role))
            writer.flush()

        return 0
    finally:
        if previous_sigint is not None:
            try:
                signal.signal(signal.SIGINT, previous_sigint)
            except (ValueError, OSError):
                pass


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Persistent harness-neutral terminal for one-shot harnesses."
    )
    parser.add_argument("--role", required=True, help="Role key (display identity)")
    parser.add_argument("--harness", required=True, help="Harness key (dsh/codex/...)")
    parser.add_argument("--model-target", default="", help="Already-resolved model target (identity)")
    parser.add_argument("--flow", default="", help="Optional opaque context label")
    parser.add_argument("--cwd", default=None, help="Working directory for invocations")
    parser.add_argument("--heartbeat-interval", type=float, default=15.0,
                        help="Seconds between heartbeats while a harness runs")
    args = parser.parse_args(argv)

    cwd = args.cwd or os.getcwd()
    reader = FrameReader(sys.stdin.buffer)
    cancel_event = threading.Event()
    return run_terminal(
        role=args.role,
        harness=args.harness,
        model_target=args.model_target,
        cwd=cwd,
        flow=args.flow,
        reader=reader,
        writer=sys.stdout,
        heartbeat_interval=args.heartbeat_interval,
        status_info={
            "flow": args.flow,
            "model_target": args.model_target,
        },
        cancel_event=cancel_event,
    )


if __name__ == "__main__":
    sys.exit(main())
