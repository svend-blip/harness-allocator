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
from .invoke import LiveRenderer, execute
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


def _parse_events(out):
    """The JSONL events in ``out``, or ``None`` if it is not a pure event stream.

    If ANY non-empty line is not a JSON object carrying an ``event`` key the
    output is not this protocol — a harness with another output shape, or a
    mixed stream — and must be shown exactly as it was, never half-rendered.
    """
    events = []
    for line in out.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            obj = json.loads(stripped)
        except ValueError:
            return None
        if not isinstance(obj, dict) or "event" not in obj:
            return None
        events.append(obj)
    return events or None


def render_child_output(out):
    """Render a harness's finished JSONL event stream as readable text.

    The one-shot harness is launched with ``--output jsonl`` so the runner
    can read status and exit code by machine; the pane, though, is read by
    a person. The same :class:`~harness_allocator.invoke.LiveRenderer` that
    paints the pane while the harness runs is driven here over the whole
    buffer: assistant deltas become prose (reasoning collapsed to a
    marker), each tool result one line, status changes and the completion
    kept, and a closing summary counts model requests, tool calls and tool
    errors. Output that is not a pure event stream is returned verbatim.
    The full event stream stays in the harness's own session log on disk.
    """
    events = _parse_events(out)
    if events is None:
        return out
    renderer = LiveRenderer()
    rendered = []
    for event in events:
        rendered.extend(renderer.feed_event(event))
    rendered.extend(renderer.flush())
    rendered.append(renderer.summary())
    return "\n".join(rendered)


def child_output_summary(out):
    """The ``[turns] …`` line for a finished event stream, or ``None``.

    Used when the pane already showed the stream live and only the closing
    count is still owed; it is derived from the same renderer so it can
    never disagree with what was printed.
    """
    events = _parse_events(out)
    if events is None:
        return None
    renderer = LiveRenderer()
    for event in events:
        renderer.feed_event(event)
    return renderer.summary()


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

    # OUTPUT arrives from the runner's drainer thread while HEARTBEAT comes
    # from its poll loop; one lock keeps each pane line whole. live_lines
    # counts what this request already showed, so the completion block
    # knows whether the stream was painted live or still needs rendering.
    write_lock = threading.Lock()
    live_lines = {"count": 0}

    def on_event(kind, payload):
        with write_lock:
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
            elif kind == "OUTPUT":
                # One rendered pane line (invoke.LiveRenderer), the moment
                # it is complete: prose as it streams, a tool result as it
                # lands, a status change as it happens.
                writer.write(str(payload.get("text", "")) + "\n")
                live_lines["count"] += 1
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
            live_lines["count"] = 0
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
            if out and live_lines["count"]:
                # The stream was painted while it ran; only the closing
                # count is owed. A runner whose output was not an event
                # stream passed through verbatim live and owes nothing.
                summary = child_output_summary(out)
                if summary:
                    writer.write(summary + "\n")
            elif out:
                # No OUTPUT events came — a runner that renders nothing
                # live (a communicate()-only process, a fake in tests) —
                # so the finished buffer is rendered here, as before.
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
