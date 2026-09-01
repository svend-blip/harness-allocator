"""render_child_output: the pane shows a harness's JSONL stream as text."""
import json

from harness_allocator.terminal import render_child_output


def _jsonl(*events):
    return "\n".join(json.dumps(e) for e in events)


def _base(event, **kw):
    e = {"protocol_version": "1", "event": event, "timestamp": "t", "session_id": "01a05ecc-9645"}
    e.update(kw)
    return e


def test_renders_stream_as_prose_tools_status_and_summary():
    out = _jsonl(
        _base("started", config={"model": "MiniMax-M3", "workspace": "/w", "permission": "WORKSPACE_WRITE"}),
        _base("model_request"),
        _base("status", status="STREAMING"),
        _base("assistant_stream", role="assistant", delta="<think>plan plan</think>Reading "),
        _base("assistant_stream", role="assistant", delta="the handoff."),
        _base("tool_call", call_id="c1", tool="shell"),
        _base("tool_result", call_id="c1", tool="shell", tool_result_status="ok",
              content=json.dumps({"exit_code": 0, "stdout": "x", "stderr": "", "duration": "46ms"})),
        _base("model_request"),
        _base("tool_call", call_id="c2", tool="read_file"),
        _base("tool_result", call_id="c2", tool="read_file", tool_result_status="error",
              content=json.dumps({"kind": "path_escape", "message": "perm.Authorize rejected call read_file", "call": {}})),
        _base("status", status="COMPLETED"),
        _base("completed"),
    )
    text = render_child_output(out)
    lines = text.splitlines()
    assert lines[0] == "[started] session 01a05ecc model=MiniMax-M3 workspace=/w permission=WORKSPACE_WRITE"
    assert "[thinking: 9 chars]Reading the handoff." in lines
    assert "[tool] shell exit=0 (46ms)" in lines
    assert "[tool] read_file ERROR path_escape: perm.Authorize rejected call read_file" in lines
    assert "[status] COMPLETED" in lines
    assert "[completed] exit=0" in lines
    assert lines[-1] == "[turns] 2 model requests, 2 tool calls, 1 tool errors"
    assert "STREAMING" not in text
    assert "protocol_version" not in text


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
              content=json.dumps({"kind": "unknown_tool", "message": "no tool named " + bad, "call": {}})),
        _base("completed", exit_code=0),
    )
    text = render_child_output(out)
    assert "[tool] " + bad + " ERROR unknown_tool: no tool named " in text
    assert text.splitlines()[-1] == "[turns] 0 model requests, 1 tool calls, 1 tool errors"


def test_non_jsonl_output_passes_through_verbatim():
    out = "plain harness output\nsecond line"
    assert render_child_output(out) == out


def test_mixed_output_passes_through_verbatim_not_half_rendered():
    out = json.dumps(_base("completed")) + "\nwarning: something\n"
    assert render_child_output(out) == out


def test_json_without_event_key_passes_through():
    out = json.dumps({"status": "ok"})
    assert render_child_output(out) == out


def test_empty_output_is_returned_unchanged():
    assert render_child_output("") == ""
