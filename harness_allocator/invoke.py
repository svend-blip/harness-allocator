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
``on_event`` callback without exposing private reasoning. The final SUCCESS or
ERROR is reported by the terminal from the returned result, not by ``on_event``.
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import threading
import time as _time

from .adapter import build_qwen_env, build_task_argv
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
            cancel_grace_seconds=CANCEL_GRACE_SECONDS) -> dict:
    """Run ``task`` through ``harness`` and return the contract result.

    ``model_target`` is the already-resolved target; it is never resolved or
    substituted here. ``harness`` may be omitted when ``role`` already carries
    the harness key. ``cwd`` is the working directory (``None`` = inherit the
    caller's). ``timeout`` is an optional per-invocation cap in seconds.
    ``heartbeat_interval`` controls heartbeat cadence (default
    :data:`DEFAULT_HEARTBEAT_INTERVAL`). ``on_event``, when given, is called as
    ``on_event(kind, payload)`` with ``kind`` in ``{RUNNING, "HEARTBEAT"}``.
    ``cancel_event`` and ``cancel_callback`` are optional cooperative hooks
    used by the persistent terminal to stop this invocation on Ctrl+C.
    ``cfg`` is injectable for tests.

    The full ``task`` (any size, any number of embedded newlines) is delivered
    to the harness as exactly one subprocess argv element and produces
    exactly one harness invocation.
    """
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
        # Endpoint wiring (D3 — Run 022 / HA-2): the qwen child env is
        # {**parent_env, **build_qwen_env(...)} so the binary stays
        # resolvable via PATH. When build_qwen_env returns an empty
        # dict, pass env=None (inherit) so the four existing harnesses
        # and the default qwen config stay byte-identical.
        if harness_key == "qwen":
            qwen_env = build_qwen_env(model_target=model_target, cfg=cfg)
            env = {**os.environ, **qwen_env} if qwen_env else None
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
    can never deadlock the heartbeat loop.
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

    def _capture():
        out, err = proc.communicate()
        captured["out"] = out or ""
        captured["err"] = err or ""

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
        if now - last_hb >= heartbeat_interval:
            if on_event:
                on_event(
                    "HEARTBEAT",
                    {**ctx, "pid": pid, "elapsed": now - start, "process_alive": True},
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
    return {
        "status": status,
        "output": captured.get("out", ""),
        "error": err,
        "elapsed": elapsed,
        "pid": pid,
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



def execute_spec(spec, cfg=None):
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
    # (1) Validate first; typed errors propagate BEFORE any subprocess.
    spec.validate()

    harness_key = (spec.harness or "").strip()

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
    #     child env threads {**parent_env, **build_qwen_env(...)} so the
    #     binary stays resolvable via PATH; non-qwen specs pass env=None
    #     (inherit) and stay byte-identical to the existing four-harness
    #     facade (D3 — Run 022 / HA-2).
    cwd = spec.working_directory or os.getcwd()
    if harness_key == "qwen":
        qwen_env = build_qwen_env(model_target=spec.model_reference, cfg=cfg)
        env = {**os.environ, **qwen_env} if qwen_env else None
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
