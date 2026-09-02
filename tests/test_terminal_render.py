"""The pane: a harness's JSONL stream as compact text, coloured only when asked."""
import io
import json

from harness_allocator.status import CANCELLED, ERROR, SUCCESS
from harness_allocator.terminal import (
    child_output_stats,
    color_enabled,
    paint,
    render_child_output,
    render_completion,
    run_terminal,
)
from harness_allocator.transport import FrameReader, compute_identity, encode_request


def _jsonl(*events):
    return "\n".join(json.dumps(e) for e in events)


def _base(event, **kw):
    e = {"protocol_version": "1", "event": event, "timestamp": "t", "session_id": "01a05ecc-9645"}
    e.update(kw)
    return e


def _tool_ok(duration, exit_code=0):
    return json.dumps({"exit_code": exit_code, "stdout": "x", "stderr": "", "duration": duration})


def _tool_err(kind, message):
    return json.dumps({"kind": kind, "message": message, "call": {}})


# ── render_child_output / child_output_stats ────────────────────────


def test_renders_stream_as_prose_tools_and_informative_status_only():
    out = _jsonl(
        _base("started", config={"model": "MiniMax-M3", "workspace": "/w", "permission": "WORKSPACE_WRITE"}),
        _base("model_request"),
        _base("status", status="STREAMING"),
        _base("assistant_stream", role="assistant", delta="<think>plan plan</think>Reading "),
        _base("assistant_stream", role="assistant", delta="the handoff."),
        _base("tool_call", call_id="c1", tool="shell"),
        _base("tool_result", call_id="c1", tool="shell", tool_result_status="ok", content=_tool_ok("46ms")),
        _base("model_request"),
        _base("tool_call", call_id="c2", tool="read_file"),
        _base("tool_result", call_id="c2", tool="read_file", tool_result_status="error",
              content=_tool_err("path_escape", "perm.Authorize rejected call read_file")),
        _base("status", status="COMPLETED"),
        _base("completed"),
    )
    text = render_child_output(out)
    assert text.splitlines() == [
        "[started] session 01a05ecc · MiniMax-M3 · /w · WORKSPACE_WRITE",
        "[thinking: 9 chars]",
        "Reading the handoff.",
        "[tool] shell ok (46 ms)",
        "[tool] read_file ERROR path_escape (absolute path rejected at the permission gate)",
    ]
    assert "STREAMING" not in text and "COMPLETED" not in text and "[completed]" not in text
    assert "protocol_version" not in text
    assert child_output_stats(out) == {
        "tail": "2 req / 2 calls / 1 err", "exit_code": 0, "last_status": None,
        "model_requests": 2, "tool_calls": 2, "tool_errors": 1,
    }


def test_unterminated_thinking_is_marked_as_a_defect():
    out = _jsonl(
        _base("model_request"),
        _base("assistant_stream", role="assistant", delta="<think>func Load(root string)"),
        _base("status", status="COMPLETED"),
        _base("completed", exit_code=0),
    )
    text = render_child_output(out)
    assert "[thinking, UNTERMINATED: 22 chars]" in text
    assert "func Load" not in text


def test_unknown_tool_name_is_shown_with_its_kind():
    bad = '"cat /home/x/a.go]<]minimax['
    out = _jsonl(
        _base("tool_call", call_id="c9", tool=bad),
        _base("tool_result", call_id="c9", tool=bad, tool_result_status="error",
              content=_tool_err("unknown_tool", "no tool named " + bad)),
        _base("completed", exit_code=0),
    )
    text = render_child_output(out)
    assert text == "[tool] " + bad + " ERROR unknown_tool: no tool named " + bad
    assert child_output_stats(out)["tail"] == "0 req / 1 calls / 1 err"


def test_repeated_tool_results_render_as_one_line_in_the_finished_block():
    events = [_base("model_request")]
    for i in range(3):
        events.append(_base("tool_call", call_id=f"c{i}", tool="read_file"))
        events.append(_base("tool_result", call_id=f"c{i}", tool="read_file", tool_result_status="error",
                            content=_tool_err("path_escape", "perm.Authorize rejected call read_file")))
        events.append(_base("model_request"))
        events.append(_base("status", status="STREAMING"))
    for i, dur in enumerate(("2.021236ms", "1.475391ms", "1.485181ms")):
        events.append(_base("tool_call", call_id=f"s{i}", tool="shell"))
        events.append(_base("tool_result", call_id=f"s{i}", tool="shell", tool_result_status="ok",
                            content=_tool_ok(dur)))
        events.append(_base("model_request"))
    events += [_base("status", status="COMPLETED"), _base("completed", exit_code=0)]
    assert render_child_output(_jsonl(*events)).splitlines() == [
        "[tool] read_file ×3 ERROR path_escape (absolute path rejected at the permission gate)",
        "[tool] shell ×3 ok (max 2 ms)",
    ]


def test_stats_carry_the_last_informative_status_and_the_exit_code():
    out = _jsonl(
        _base("model_request"),
        _base("status", status="TOOL_DISPATCH_OVERFLOW: max-turns 30 exceeded"),
        _base("status", status="FAILED"),
        _base("completed", exit_code=1),
    )
    stats = child_output_stats(out)
    assert stats["last_status"] == "TOOL_DISPATCH_OVERFLOW: max-turns 30 exceeded"
    assert stats["exit_code"] == 1
    assert render_child_output(out) == "[status] TOOL_DISPATCH_OVERFLOW: max-turns 30 exceeded"


def test_non_jsonl_output_passes_through_verbatim():
    out = "plain harness output\nsecond line"
    assert render_child_output(out) == out
    assert child_output_stats(out) is None


def test_mixed_output_passes_through_verbatim_not_half_rendered():
    out = json.dumps(_base("completed")) + "\nwarning: something\n"
    assert render_child_output(out) == out


def test_json_without_event_key_passes_through():
    out = json.dumps({"status": "ok"})
    assert render_child_output(out) == out


def test_empty_output_is_returned_unchanged():
    assert render_child_output("") == ""
    assert child_output_stats("") is None


# ── the one completion line ─────────────────────────────────────────


def test_completion_is_one_line_per_status():
    stats = {"tail": "11 req / 17 calls / 3 err", "exit_code": 0, "last_status": None}
    assert render_completion(SUCCESS, "ha-000001", 88.84, stats) == \
        ("[SUCCESS] ha-000001 · 88.8s · 11 req / 17 calls / 3 err", "success")
    assert render_completion(SUCCESS, "ha-2", 0.5, None) == ("[SUCCESS] ha-2 · 0.5s", "success"), \
        "a runner whose output was not an event stream has no counts"
    assert render_completion(CANCELLED, "ha-3", 12.0, stats) == \
        ("[CANCELLED] ha-3 · 12.0s · 11 req / 17 calls / 3 err", "cancelled")
    failed = {"tail": "31 req / 30 calls / 0 err", "exit_code": 1,
              "last_status": "TOOL_DISPATCH_OVERFLOW: max-turns 30 exceeded"}
    assert render_completion(ERROR, "ha-4", 3.25, failed) == \
        ("[FAILED] ha-4 · 3.2s · 31 req / 30 calls / 0 err · exit=1 · "
         "TOOL_DISPATCH_OVERFLOW: max-turns 30 exceeded", "failed")
    assert render_completion(ERROR, "ha-5", 1.0, failed, exit_code=137) == \
        ("[FAILED] ha-5 · 1.0s · 31 req / 30 calls / 0 err · exit=137 · "
         "TOOL_DISPATCH_OVERFLOW: max-turns 30 exceeded", "failed"), \
        "the runner's own exit code wins over the one the child reported"
    assert render_completion(ERROR, "ha-6", 0.0, None) == ("[FAILED] ha-6 · 0.0s", "failed")


# ── colour ──────────────────────────────────────────────────────────


def test_paint_uses_a_small_palette_and_leaves_prose_alone():
    assert paint("tool", "[tool] shell ok (47 ms)") == "\x1b[90m[tool] shell ok (47 ms)\x1b[0m"
    assert paint("thinking", "[thinking: 3 chars]") == "\x1b[90m[thinking: 3 chars]\x1b[0m"
    assert paint("tool_error", "[tool] x ERROR y") == "\x1b[31m[tool] x ERROR y\x1b[0m"
    assert paint("failed", "[FAILED] ha-1 · 1.0s") == "\x1b[31m[FAILED] ha-1 · 1.0s\x1b[0m"
    assert paint("success", "[SUCCESS] ha-1 · 1.0s") == "\x1b[32m[SUCCESS] ha-1 · 1.0s\x1b[0m"
    assert paint("cancelled", "[CANCELLED] ha-1") == "\x1b[33m[CANCELLED] ha-1\x1b[0m"
    assert paint("status", "[status] SOMETHING") == "\x1b[33m[status] SOMETHING\x1b[0m"
    assert paint("status", "[status] TOOL_DISPATCH_OVERFLOW: max-turns 30 exceeded") == \
        "\x1b[31m[status] TOOL_DISPATCH_OVERFLOW: max-turns 30 exceeded\x1b[0m"
    for kind in ("dispatch", "running", "started", "heartbeat"):
        assert paint(kind, "x") == "\x1b[90mx\x1b[0m", kind
    assert paint("prose", "Reading the handoff.") == "Reading the handoff."
    assert paint("passthrough", "warning: odd") == "warning: odd"
    assert paint("tool", "[tool] shell ok", color=False) == "[tool] shell ok"
    assert paint("tool", "") == ""


def test_color_is_off_unless_a_tty_or_asked_for(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("HARNESS_TERMINAL_COLOR", raising=False)
    plain = io.StringIO()
    assert color_enabled(plain) is False, "a StringIO is not a tty"
    assert color_enabled(plain, color=True) is True
    monkeypatch.setenv("HARNESS_TERMINAL_COLOR", "1")
    assert color_enabled(plain) is True
    assert color_enabled(plain, color=False) is False, "an explicit choice wins over the env"
    monkeypatch.setenv("NO_COLOR", "1")
    assert color_enabled(plain) is False, "NO_COLOR wins over HARNESS_TERMINAL_COLOR"
    assert color_enabled(plain, color=True) is True

    class _Tty(io.StringIO):
        def isatty(self):
            return True
    monkeypatch.delenv("NO_COLOR")
    monkeypatch.delenv("HARNESS_TERMINAL_COLOR")
    assert color_enabled(_Tty()) is True


# ── the whole pane, driven by a fake runner ─────────────────────────


def _sample_stream():
    """A finished simple-harness run in the shape the Human's transcript had."""
    started = _base("started", session_id="01a061bb-7c2e",
                    config={"model": "MiniMax-M3", "workspace": "/w/FlowRunner",
                            "permission": "WORKSPACE_WRITE"})
    events = [started, _base("model_request"), _base("status", status="STREAMING"),
              _base("assistant_stream", delta="<think>" + "p" * 371 + "</think>\n\n")]
    for i in range(3):
        events.append(_base("tool_call", call_id=f"r{i}", tool="read_file"))
        events.append(_base("tool_result", call_id=f"r{i}", tool="read_file", tool_result_status="error",
                            content=_tool_err("path_escape",
                                              "perm.Authorize rejected call read_file at stage path: absolute_path")))
        events.append(_base("model_request"))
        events.append(_base("status", status="STREAMING"))
    for i, dur in enumerate(("2.021236ms", "1.475391ms", "1.485181ms", "0.6ms", "1.9ms")):
        events.append(_base("tool_call", call_id=f"s{i}", tool="shell"))
        events.append(_base("tool_result", call_id=f"s{i}", tool="shell", tool_result_status="ok",
                            content=_tool_ok(dur)))
        events.append(_base("model_request"))
    events += [
        _base("assistant_stream", delta="Now the failing test.\n"),
        _base("tool_call", call_id="s9", tool="shell"),
        _base("tool_result", call_id="s9", tool="shell", tool_result_status="ok", content=_tool_ok("47.2ms")),
        _base("model_request"),
        _base("tool_call", call_id="w1", tool="write_file"),
        _base("tool_result", call_id="w1", tool="write_file", tool_result_status="error",
              content=_tool_err("schema_violation", "start_line is not an int")),
        _base("model_request"),
        _base("assistant_stream", delta="Reading the handoff and running the tests.\n"),
        {"heartbeat": {"phase": "STREAMING", "elapsed": 35.4, "activity": "reply #3 (18663 chars)",
                       "model_requests": 3, "tool_calls": 9, "tool_errors": 3}},
        _base("assistant_stream", delta="Done.\n"),
        _base("status", status="COMPLETED"),
        _base("completed", exit_code=0),
    ]
    return events


def _renderer_backed_runner(events, status=SUCCESS, elapsed=88.84, exit_code=0):
    """A runner that paints the pane the way run_argv does: one OUTPUT per fragment,
    a heartbeat that first releases a held tool group, the raw stream as output."""
    from harness_allocator.invoke import LiveRenderer

    def runner(**kwargs):
        on_event = kwargs["on_event"]
        rid = kwargs["request_id"]
        on_event("RUNNING", {"request_id": rid, "pid": 1930579, "elapsed": 0.0, "process_alive": True})
        renderer = LiveRenderer()
        raw = []

        def emit(fragments):
            for text, kind in fragments:
                on_event("OUTPUT", {"request_id": rid, "pid": 1930579, "text": text, "kind": kind})
        for event in events:
            if "heartbeat" in event:
                emit(renderer.flush_tools())
                on_event("HEARTBEAT", {"request_id": rid, "pid": 1930579, "process_alive": True,
                                       **event["heartbeat"]})
                continue
            raw.append(json.dumps(event))
            emit(renderer.feed_event(event))
        emit(renderer.flush())
        return {"status": status, "output": "\n".join(raw) + "\n", "error": "",
                "elapsed": elapsed, "pid": 1930579, "exit_code": exit_code, "request_id": rid}
    return runner


def _drive(task, runner, color=None):
    reader = FrameReader(io.BytesIO(encode_request("ha-000001", task)))
    writer = io.StringIO()
    run_terminal(role="9000-execution-decomposer", harness="simple-harness", model_target="MiniMax-M3",
                 cwd="/w/FlowRunner", reader=reader, writer=writer,
                 runner=runner, color=color)
    return writer.getvalue()


def _pane_between_dispatch_and_ready(out):
    body = out[out.rfind("\n", 0, out.index("[DISPATCH]")) + 1:]   # from the start of that line (SGR included)
    body = body[:body.index("\nStatus: READY")]
    return [line for line in body.splitlines() if line]


def test_pane_is_compact_one_line_per_thing_plain_by_default():
    task = "line\n" * 18 + "tail"
    ident = compute_identity("ha-000001", task)
    out = _drive(task, _renderer_backed_runner(_sample_stream()))
    assert _pane_between_dispatch_and_ready(out) == [
        f"[DISPATCH] ha-000001 · {ident.chars} chars / {ident.lines} lines · sha256 {ident.sha256[:8]}",
        "[RUNNING] simple-harness / MiniMax M3 · pid 1930579",
        "[started] session 01a061bb · MiniMax-M3 · /w/FlowRunner · WORKSPACE_WRITE",
        "[thinking: 371 chars]",
        "[tool] read_file ×3 ERROR path_escape (absolute path rejected at the permission gate)",
        "[tool] shell ×5 ok (max 2 ms)",
        "Now the failing test.",
        "[tool] shell ok (47 ms)",
        "[tool] write_file ERROR schema_violation: start_line is not an int",
        "Reading the handoff and running the tests.",
        "[HEARTBEAT - STREAMING] · ha-000001 · 35s · reply #3 (18663 chars) · 3 req / 9 calls / 3 err",
        "Done.",
        "[SUCCESS] ha-000001 · 88.8s · 11 req / 10 calls / 4 err",
    ]
    assert "\x1b[" not in out, "no colour on a non-tty writer by default"
    assert "elapsed:" not in out and "[completed]" not in out and "[turns]" not in out
    assert "[status] COMPLETED" not in out


def test_pane_is_coloured_when_asked_prose_stays_plain():
    task = "task"
    out = _drive(task, _renderer_backed_runner(_sample_stream()), color=True)
    lines = _pane_between_dispatch_and_ready(out)
    assert lines[0].startswith("\x1b[90m[DISPATCH] ha-000001 · ") and lines[0].endswith("\x1b[0m")
    assert lines[1] == "\x1b[90m[RUNNING] simple-harness / MiniMax M3 · pid 1930579\x1b[0m"
    assert lines[3] == "\x1b[90m[thinking: 371 chars]\x1b[0m"
    assert lines[4] == ("\x1b[31m[tool] read_file ×3 ERROR path_escape "
                        "(absolute path rejected at the permission gate)\x1b[0m")
    assert lines[5] == "\x1b[90m[tool] shell ×5 ok (max 2 ms)\x1b[0m"
    assert lines[6] == "Now the failing test.", "prose is never painted"
    assert lines[9] == "Reading the handoff and running the tests."
    assert lines[10].startswith("\x1b[90m[HEARTBEAT - STREAMING]")
    assert lines[-1] == "\x1b[32m[SUCCESS] ha-000001 · 88.8s · 11 req / 10 calls / 4 err\x1b[0m"


def test_pane_colour_follows_the_environment(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("HARNESS_TERMINAL_COLOR", "1")
    out = _drive("task", _renderer_backed_runner(_sample_stream()))
    assert "\x1b[32m[SUCCESS]" in out
    monkeypatch.setenv("NO_COLOR", "1")
    out = _drive("task", _renderer_backed_runner(_sample_stream()))
    assert "\x1b[" not in out


def test_failed_run_is_one_red_line_with_exit_and_last_status():
    events = [
        _base("model_request"),
        _base("tool_call", call_id="c1", tool="shell"),
        _base("tool_result", call_id="c1", tool="shell", tool_result_status="ok", content=_tool_ok("1.2s")),
        _base("status", status="TOOL_DISPATCH_OVERFLOW: max-turns 30 exceeded"),
        _base("status", status="FAILED"),
        _base("completed", exit_code=1),
    ]
    out = _drive("task", _renderer_backed_runner(events, status=ERROR, elapsed=3.25, exit_code=1), color=True)
    lines = _pane_between_dispatch_and_ready(out)
    assert lines[2:] == [
        "\x1b[90m[tool] shell ok (1.2 s)\x1b[0m",
        "\x1b[31m[status] TOOL_DISPATCH_OVERFLOW: max-turns 30 exceeded\x1b[0m",
        "\x1b[31m[FAILED] ha-000001 · 3.2s · 1 req / 1 calls / 0 err · exit=1 · "
        "TOOL_DISPATCH_OVERFLOW: max-turns 30 exceeded\x1b[0m",
    ]
    assert "[status] FAILED" not in out
