"""Heartbeats carry the harness's live phase, read from its stdout stream."""
import io
import json
import threading

import harness_allocator.invoke as inv
from harness_allocator.invoke import ProgressTracker, run_argv


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
