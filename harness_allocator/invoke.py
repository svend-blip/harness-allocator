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

from .adapter import build_task_argv
from .definition import model_target_identity, resolve_harness, resolve_role_key
from .status import CANCELLED, ERROR, RUNNING, SUCCESS
from .transport import compute_identity, make_request_id

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
             cancel_grace_seconds=CANCEL_GRACE_SECONDS) -> dict:
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
