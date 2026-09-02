"""Heartbeats carry the harness's live phase, read from its stdout stream."""
import io
import json
import threading

import harness_allocator.invoke as inv
from harness_allocator.invoke import LiveRenderer, ProgressTracker, run_argv


def _ev(event, **kw):
    e = {"protocol_version": "1", "event": event, "timestamp": "t", "session_id": "s"}
    e.update(kw)
    return json.dumps(e) + "\n"


def test_tracker_follows_the_event_stream():
    t = ProgressTracker()
    assert t.snapshot()["phase"] == "IDLE"
    t.feed(_ev("started", config={}))
    t.feed(_ev("model_request"))
    assert t.snapshot()["activity"] == "model request #1"
    t.feed(_ev("assistant_stream", delta="abc"))
    t.feed(_ev("assistant_stream", delta="de"))
    assert t.snapshot() == {"phase": "STREAMING", "activity": "streaming reply #1 (5 chars)",
                            "model_requests": 1, "tool_calls": 0, "tool_errors": 0}
    t.feed(_ev("tool_call", call_id="c1", tool="shell"))
    assert t.snapshot()["phase"] == "TOOL"
    assert t.snapshot()["activity"] == "tool shell #1 running"
    t.feed(_ev("tool_result", call_id="c1", tool_result_status="error", content="{}"))
    s = t.snapshot()
    assert s["activity"] == "tool shell #1 error" and s["tool_errors"] == 1
    t.feed(_ev("status", status="STREAMING"))
    assert t.snapshot()["phase"] == "TOOL", "STREAMING status is noise, not a phase change"
    t.feed(_ev("status", status="TOOL_DISPATCH_OVERFLOW: max-turns 30 exceeded"))
    assert t.snapshot()["activity"].startswith("TOOL_DISPATCH_OVERFLOW")
    t.feed(_ev("completed", exit_code=0))
    assert t.snapshot()["activity"] == "completed"


def test_tracker_counts_non_json_output_instead_of_parsing_it():
    t = ProgressTracker()
    t.feed("plain line\n")
    t.feed("{not json\n")
    t.feed('{"no_event_key": 1}\n')
    assert t.snapshot() == {"phase": "OUTPUT", "activity": "3 output lines",
                            "model_requests": 0, "tool_calls": 0, "tool_errors": 0}


class _StreamingProc:
    """A fake process with real line-readable pipes and a controllable exit."""
    pid = 4343

    def __init__(self, stdout_text, stderr_text="", polls_before_exit=3):
        self.stdout = io.StringIO(stdout_text)
        self.stderr = io.StringIO(stderr_text)
        self._polls = polls_before_exit
        self.returncode = None
        self._done = threading.Event()

    def poll(self):
        import time
        time.sleep(0.02)  # real time: let the drainer thread consume the stream
        if self._polls > 0:
            self._polls -= 1
            return None
        self.returncode = 0
        return 0

    def wait(self):
        return 0


def test_run_argv_heartbeat_reports_phase_from_live_stdout(monkeypatch):
    stream = (_ev("started", config={}) + _ev("model_request")
              + _ev("tool_call", call_id="c1", tool="shell")
              + _ev("tool_result", call_id="c1", tool_result_status="ok", content="{}")
              + _ev("model_request") + _ev("status", status="COMPLETED") + _ev("completed"))
    proc = _StreamingProc(stream, stderr_text="warn\n", polls_before_exit=3)

    class _Clock:
        now = 0.0

        def monotonic(self):
            return self.now

        def sleep(self, s):
            self.now += s
    clock = _Clock()
    monkeypatch.setattr(inv.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(inv, "_time", clock)
    events = []
    result = run_argv(["x"], cwd=".", heartbeat_interval=0.1,
                      on_event=lambda k, p: events.append((k, dict(p))),
                      event_context={"request_id": "ha-9"})
    hbs = [p for k, p in events if k == "HEARTBEAT"]
    assert hbs, "a heartbeat must have fired while the fake process was alive"
    last = hbs[-1]
    assert last["phase"] in ("MODEL", "TOOL", "STATUS")
    assert last["model_requests"] >= 1
    assert last["tool_calls"] == 1
    assert "activity" in last
    assert result["status"] == inv.SUCCESS
    assert result["output"] == stream
    assert result["error"] == "warn\n"


def test_run_argv_keeps_the_communicate_path_for_pipeless_processes(monkeypatch):
    class _Legacy:
        pid = 1

        returncode = None

        def __init__(self):
            self._polls = [None, 0]

        def poll(self):
            self.returncode = self._polls.pop(0)
            return self.returncode

        def communicate(self):
            return "legacy out\n", "legacy err"
    monkeypatch.setattr(inv.subprocess, "Popen", lambda *a, **k: _Legacy())
    result = run_argv(["x"], cwd=".", heartbeat_interval=10.0)
    assert result["output"] == "legacy out\n"
    assert result["error"] == "legacy err"


# ── LiveRenderer: what the pane prints, the moment it can ───────────

_TOOL_OK = json.dumps({"exit_code": 0, "stdout": "x", "stderr": "", "duration": "46ms"})


def test_live_renderer_holds_prose_until_a_line_is_complete():
    r = LiveRenderer()
    assert r.feed(_ev("model_request")) == []
    assert r.feed(_ev("assistant_stream", delta="Reading ")) == [], "a partial line is not shown yet"
    assert r.feed(_ev("assistant_stream", delta="the handoff.\nNext ")) == ["Reading the handoff."]
    assert r.feed(_ev("assistant_stream", delta="step.\n\n\nDone.\n\n")) == ["Next step.", "", "", "Done."]
    assert r.flush() == [], "trailing blank lines are not printed"
    assert r.feed(_ev("assistant_stream", delta="\n\n  tail without newline")) == []
    assert r.flush() == ["tail without newline"], "leading blanks and indent of a block are dropped"


def test_live_renderer_suppresses_thinking_and_marks_it_when_the_block_closes():
    r = LiveRenderer()
    assert r.feed(_ev("assistant_stream", delta="<think>plan\nplan")) == [], "no thinking is ever printed"
    assert r.feed(_ev("assistant_stream", delta="</think>Reading the handoff.\n")) == \
        ["[thinking: 9 chars]Reading the handoff."]
    # A tag split across deltas is still one tag.
    assert r.feed(_ev("assistant_stream", delta="<thi")) == []
    assert r.feed(_ev("assistant_stream", delta="nk>abc</th")) == []
    assert r.feed(_ev("assistant_stream", delta="ink>x\n")) == ["[thinking: 3 chars]x"]
    # A lone '<' that turns out not to be a tag is ordinary prose.
    assert r.feed(_ev("assistant_stream", delta="a <")) == []
    assert r.feed(_ev("assistant_stream", delta=" b\n")) == ["a < b"]


def test_live_renderer_marks_an_unterminated_think_block_as_a_defect():
    r = LiveRenderer()
    assert r.feed(_ev("assistant_stream", delta="<think>func Load(root string)")) == []
    assert r.feed(_ev("tool_call", call_id="c1", tool="shell")) == \
        ["[thinking, UNTERMINATED: 22 chars]"]
    assert "func Load" not in "".join(r.flush())
    r2 = LiveRenderer()
    r2.feed(_ev("assistant_stream", delta="<think>abc"))
    assert r2.flush() == ["[thinking, UNTERMINATED: 3 chars]"], "the stream ended inside the block"


def test_live_renderer_renders_each_event_as_one_line_when_it_lands():
    r = LiveRenderer()
    assert r.feed(_ev("started", config={"model": "MiniMax-M3", "workspace": "/w",
                                         "permission": "WORKSPACE_WRITE"})) == \
        ["[started] session s model=MiniMax-M3 workspace=/w permission=WORKSPACE_WRITE"]
    assert r.feed(_ev("status", status="STREAMING")) == [], "STREAMING is noise"
    assert r.feed(_ev("tool_call", call_id="c1", tool="shell")) == []
    assert r.feed(_ev("tool_result", call_id="c1", tool_result_status="ok", content=_TOOL_OK)) == \
        ["[tool] shell exit=0 (46ms)"]
    assert r.feed(_ev("tool_call", call_id="c2", tool="read_file")) == []
    assert r.feed(_ev("tool_result", call_id="c2", tool_result_status="error",
                      content=json.dumps({"kind": "path_escape", "message": "perm.Authorize rejected"}))) == \
        ["[tool] read_file ERROR path_escape: perm.Authorize rejected"]
    assert r.feed(_ev("status", status="COMPLETED")) == ["[status] COMPLETED"]
    assert r.feed(_ev("completed", exit_code=0)) == ["[completed] exit=0"]
    assert r.feed(_ev("interrupted")) == ["[interrupted]"]
    assert r.feed(_ev("something_new")) == ["[something_new]"]
    assert r.summary() == "[turns] 0 model requests, 2 tool calls, 1 tool errors"


def test_live_renderer_flushes_prose_before_the_event_that_ends_it():
    r = LiveRenderer()
    r.feed(_ev("assistant_stream", delta="Now running the tests"))
    assert r.feed(_ev("tool_result", call_id="c1", tool="shell", tool_result_status="ok",
                      content=_TOOL_OK)) == ["Now running the tests", "[tool] shell exit=0 (46ms)"]


def test_live_renderer_passes_non_json_lines_through_verbatim():
    r = LiveRenderer()
    assert r.feed("warning: something odd\n") == ["warning: something odd"]
    assert r.feed("{not json\n") == ["{not json"]
    assert r.feed('{"no_event_key": 1}\n') == ['{"no_event_key": 1}']
    assert r.feed("   \n") == []


class _GatedStream:
    """A readable stdout whose lines are released only once ``gate`` is set."""

    def __init__(self, text, gate):
        self._buf = io.StringIO(text)
        self._gate = gate

    def readline(self):
        self._gate.wait()
        return self._buf.readline()


class _GatedProc(_StreamingProc):
    """Holds its whole stdout back until poll number ``release_at_poll``."""

    def __init__(self, stdout_text, release_at_poll, polls_before_exit):
        super().__init__("", polls_before_exit=polls_before_exit)
        self._gate = threading.Event()
        self.stdout = _GatedStream(stdout_text, self._gate)
        self._release_at_poll = release_at_poll
        self._poll_count = 0

    def poll(self):
        self._poll_count += 1
        if self._poll_count == self._release_at_poll:
            self._gate.set()
        return super().poll()


_LIVE_STREAM = (_ev("started", config={"model": "m", "workspace": "/w", "permission": "P"})
                + _ev("model_request")
                + _ev("assistant_stream", delta="Reading ")
                + _ev("assistant_stream", delta="the handoff.\n")
                + _ev("tool_call", call_id="c1", tool="shell")
                + _ev("tool_result", call_id="c1", tool_result_status="ok", content=_TOOL_OK)
                + _ev("status", status="COMPLETED")
                + _ev("completed", exit_code=0))

_LIVE_LINES = [
    "[started] session s model=m workspace=/w permission=P",
    "Reading the handoff.",
    "[tool] shell exit=0 (46ms)",
    "[status] COMPLETED",
    "[completed] exit=0",
]


def _fake_clock():
    class _Clock:
        now = 0.0

        def monotonic(self):
            return self.now

        def sleep(self, s):
            self.now += s
    return _Clock()


def test_run_argv_emits_output_fragments_in_order_and_heartbeats_only_when_idle(monkeypatch):
    # The clock advances 0.2 per poll; stdout is released on poll 3 (t=0.4)
    # and the process exits after poll 6. With a 0.5 s interval an ungated
    # heartbeat would fire at t=0.6; silence-gated, the first one is at
    # t=1.0 — after every OUTPUT fragment, and only once.
    proc = _GatedProc(_LIVE_STREAM, release_at_poll=3, polls_before_exit=6)
    clock = _fake_clock()
    monkeypatch.setattr(inv.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(inv, "_time", clock)
    events = []
    result = run_argv(["x"], cwd=".", heartbeat_interval=0.5,
                      on_event=lambda k, p: events.append((k, dict(p))),
                      event_context={"request_id": "ha-9"})
    kinds = [k for k, _ in events]
    assert kinds[0] == "RUNNING"
    assert [p["text"] for k, p in events if k == "OUTPUT"] == _LIVE_LINES
    outputs = [p for k, p in events if k == "OUTPUT"]
    assert all(p["request_id"] == "ha-9" and p["pid"] == proc.pid for p in outputs)
    hbs = [(i, p) for i, (k, p) in enumerate(events) if k == "HEARTBEAT"]
    assert len(hbs) == 1, f"one heartbeat after the quiet stretch, got {kinds}"
    assert hbs[0][0] > kinds.index("OUTPUT") + len(_LIVE_LINES) - 1, "heartbeat came after the output"
    assert abs(hbs[0][1]["elapsed"] - 1.0) < 1e-9
    assert result["status"] == inv.SUCCESS
    assert result["output"] == _LIVE_STREAM, "the raw stdout is kept unchanged"


def test_run_argv_keeps_the_output_when_the_live_callback_fails(monkeypatch):
    proc = _StreamingProc(_LIVE_STREAM, polls_before_exit=3)
    clock = _fake_clock()
    monkeypatch.setattr(inv.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(inv, "_time", clock)

    def on_event(kind, payload):
        if kind == "OUTPUT":
            raise RuntimeError("pane gone")
    result = run_argv(["x"], cwd=".", heartbeat_interval=10.0, on_event=on_event)
    assert result["status"] == inv.SUCCESS
    assert result["output"] == _LIVE_STREAM
    assert "live render failed: RuntimeError('pane gone')" in result["error"]
