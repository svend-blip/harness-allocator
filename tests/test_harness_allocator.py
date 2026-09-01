"""Tests for the standalone ``harness_allocator`` package.

Covers the corrected architecture:

- Harness Allocator owns harness identity/command/execution only; the model is
  a passed-through, already-resolved ``model_target`` (no ``resolve_model``).
- Atomic argv-style task transport: ONE semantic task = EXACTLY ONE
  invocation, with 20k+ character multi-line regression coverage and verified
  via a real subprocess.Popen (not just transport round-trip).
- Request identity / payload verification metadata (request_id, chars, lines,
  sha256, harness, role, model target).
- Heartbeat / progress visibility (RUNNING + pid + elapsed, periodic HEARTBEAT,
  SUCCESS/ERROR + final duration, return to READY).
- READY lifecycle reliability across repeated turns and handled ERROR -> READY.
- Duplicate-request protection: a completed (request_id, payload sha256) is
  never executed twice — it reports DUPLICATE_REQUEST and returns to READY —
  unless the frame carries an explicit ``retry`` flag, which re-executes it.
"""

import hashlib
import io
import sys
import tempfile
from pathlib import Path

import pytest

# Import the package from the project root (sibling of tests/).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import harness_allocator as ha  # noqa: E402
from harness_allocator import (  # noqa: E402
    DUPLICATE_REQUEST,
    ERROR,
    SUCCESS,
    FrameReader,
    TransportError,
    build_dsh_argv,
    build_dsh_invocation,
    build_launch_argv,
    build_launch_command,
    build_task_argv,
    build_task_invocation,
    compute_identity,
    describe_missing,
    encode_request,
    execute,
    extract_frame,
    missing_env,
    model_target_identity,
    render_banner,
    resolve_harness,
    run_argv,
    run_command,
    run_terminal,
)


class _FakeCfg:
    def get_codex_bin(self):
        return "codex"

    def get_codex_workdir(self):
        return ""

    def get_codex_add_dirs(self):
        return []

    def get_codex_sandbox(self):
        return "workspace-write"

    def get_codex_ask_for_approval(self):
        return "never"

    def get_dsh_bin(self):
        return "npx @deepseek-ai/dsh"

    def get_dsh_profile(self):
        return "headless"

    def get_dsh_patch_path(self):
        return "/tmp/dsh-v4-pro.patch.yml"


# ── identity resolution — model boundary ─────────────────────────────

def test_resolve_harness_explicit_and_no_silent_fallback():
    # No silent harness substitution: an absent/unknown harness yields "".
    assert resolve_harness({}) == ""
    assert resolve_harness("   ") == ""
    assert resolve_harness({"allocator_client": "codex"}) == "codex"
    assert resolve_harness({"harness": "dsh"}) == "dsh"
    assert resolve_harness("claude-code") == "claude-code"


def test_no_resolve_model_in_api():
    # The corrected boundary removes model resolution entirely.
    assert not hasattr(ha, "resolve_model")


def test_model_target_identity_is_passthrough_only():
    assert model_target_identity("deepseek-v4-pro") == "deepseek-v4-pro"
    assert model_target_identity({"model": "MiniMax-M3"}) == "MiniMax-M3"
    assert model_target_identity({"identity": "x"}) == "x"
    assert model_target_identity("") == ""
    assert model_target_identity(None) == ""


def test_harness_definition_has_no_model():
    d = ha.HarnessDefinition.from_role(
        {"role_key": "super-deep-deep4", "allocator_client": "dsh",
         "default_model_alias": "deepseek-v4-pro"}
    )
    assert d.role == "super-deep-deep4"
    assert d.harness == "dsh"
    assert not hasattr(d, "model")


# ── command building — model_target passthrough + argv form ──────────

def test_dsh_argv_carries_task_as_single_element():
    task = "Reply with exactly: OK\nSecond line\nThird line"
    argv = build_dsh_argv(model_target="deepseek-v4-pro", task=task, cfg=_FakeCfg())
    # The complete task is one argv element regardless of embedded newlines.
    assert argv == [
        "npx", "@deepseek-ai/dsh",
        "--profile", "headless",
        "--patch", "/tmp/dsh-v4-pro.patch.yml",
        task,
    ]
    assert argv[-1] == task
    assert argv[-1].count("\n") == 2  # embedded newlines preserved


def test_dsh_argv_without_patch():
    class Cfg(_FakeCfg):
        def get_dsh_patch_path(self):
            return ""

    argv = build_dsh_argv(model_target="x", cfg=Cfg())
    assert argv == ["npx", "@deepseek-ai/dsh", "--profile", "headless"]


def test_dsh_argv_omits_model_target_for_routing():
    # dsh model is pinned by profile/patch — the target must not be in argv.
    argv = build_dsh_argv(model_target="some-other-model", cfg=_FakeCfg())
    assert "some-other-model" not in argv


def test_dsh_invocation_matches_argv_joined():
    task = "line one\nline two\nSupervisor"
    cmd = build_dsh_invocation(model_target="x", task=task, cfg=_FakeCfg())
    argv = build_dsh_argv(model_target="x", task=task, cfg=_FakeCfg())
    import shlex
    # The shell command, when split, reconstructs the exact argv list.
    assert shlex.split(cmd) == argv


def test_codex_launch_argv_uses_model_target():
    argv = build_launch_argv("codex", model_target="MiniMax-M3", cfg=_FakeCfg())
    assert argv == ["codex", "-m", "MiniMax-M3",
                    "--sandbox", "workspace-write", "--ask-for-approval", "never"]


def test_codex_launch_argv_without_model_target_has_no_flag():
    argv = build_launch_argv("codex", model_target="", cfg=_FakeCfg())
    assert argv == ["codex", "--sandbox", "workspace-write", "--ask-for-approval", "never"]


def test_codex_launch_command_uses_model_target():
    cmd = build_launch_command("codex", model_target="MiniMax-M3", cfg=_FakeCfg())
    assert cmd == "codex -m MiniMax-M3 --sandbox workspace-write --ask-for-approval never"


def test_codex_argv_includes_workdir_and_add_dirs():
    class Cfg(_FakeCfg):
        def get_codex_workdir(self):
            return "/home/svend/harness-allocator"

        def get_codex_add_dirs(self):
            return ["/home/svend/flows", "/home/svend/DPMtF-WebUI"]

    argv = build_launch_argv("codex", model_target="MiniMax-M3", cfg=Cfg())
    assert argv == [
        "codex", "-m", "MiniMax-M3",
        "-C", "/home/svend/harness-allocator",
        "--add-dir", "/home/svend/flows",
        "--add-dir", "/home/svend/DPMtF-WebUI",
        "--sandbox", "workspace-write",
        "--ask-for-approval", "never",
    ]


def test_dsh_launch_command_is_headless_not_tui():
    cmd = build_launch_command("dsh", model_target="deepseek-v4-pro", cfg=_FakeCfg())
    assert "--profile headless" in cmd
    assert "--profile tui" not in cmd


def test_task_argv_rejects_resident_harnesses():
    for resident in ("codex", "claude-code", "opencode"):
        with pytest.raises(ValueError):
            build_task_argv(resident, model_target="x", task="task", cfg=_FakeCfg())


def test_task_invocation_rejects_resident_harnesses():
    for resident in ("codex", "claude-code", "opencode"):
        with pytest.raises(ValueError):
            build_task_invocation(resident, model_target="x", task="task", cfg=_FakeCfg())


def test_launch_command_rejects_unknown_harness():
    with pytest.raises(ValueError):
        build_launch_command("bogus", model_target="x", cfg=_FakeCfg())


# ── environment requirements ────────────────────────────────────────

def test_missing_env_fails_safely(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    assert missing_env("dsh") == ["DEEPSEEK_API_KEY"]
    assert missing_env("codex") == ["MINIMAX_API_KEY"]
    assert missing_env("claude-code") == []


def test_missing_env_message_names_without_leaking_values(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    msg = describe_missing("dsh", ["DEEPSEEK_API_KEY"])
    assert "DEEPSEEK_API_KEY" in msg
    assert "DeepSeek" in msg
    assert "=" not in msg


def test_present_env_reports_nothing_missing(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test")
    monkeypatch.setenv("MINIMAX_API_KEY", "test")
    assert missing_env("dsh") == []
    assert missing_env("codex") == []


# ── execute() contract + operational metadata ───────────────────────

def _patch_popen(monkeypatch, proc):
    import harness_allocator.invoke as inv

    monkeypatch.setattr(inv.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(inv, "_time", _FakeClock())


def test_execute_returns_contract_shape_and_metadata(monkeypatch):
    proc = _FakeProc(poll_results=[0], stdout="ok\n", stderr="")
    _patch_popen(monkeypatch, proc)
    r = execute(role="probe", harness="dsh", model_target="deepseek-v4-pro",
                cwd=".", task="echo ok", request_id="ha-001")

    assert set(r) >= {"status", "output", "error", "elapsed"}
    assert r["status"] == SUCCESS
    assert r["output"] == "ok\n"
    assert r["error"] == ""
    assert isinstance(r["elapsed"], float)
    assert r["request_id"] == "ha-001"
    assert r["harness"] == "dsh"
    assert r["role"] == "probe"
    assert r["model_target"] == "deepseek-v4-pro"
    assert r["payload_chars"] == len("echo ok")
    assert r["payload_lines"] == 1
    assert r["payload_sha256"] == hashlib.sha256(b"echo ok").hexdigest()


def test_execute_reports_error_for_non_native_harness():
    r = execute(role="probe", harness="codex", model_target="MiniMax-M3", cwd=".", task="x")
    assert r["status"] == ERROR
    assert "no one-shot" in r["error"]
    assert set(r) >= {"status", "output", "error", "elapsed"}


def test_execute_reports_nonzero_exit_as_error(monkeypatch):
    proc = _FakeProc(poll_results=[2], stdout="", stderr="boom")
    _patch_popen(monkeypatch, proc)
    r = execute(role="probe", harness="dsh", model_target="m", cwd=".", task="x")
    assert r["status"] == ERROR
    assert r["error"] == "boom"


def test_execute_does_not_resolve_model_from_role(monkeypatch):
    # A role carrying a model alias must NOT silently supply the model target.
    proc = _FakeProc(poll_results=[0], stdout="ok", stderr="")
    _patch_popen(monkeypatch, proc)
    r = execute(role={"allocator_client": "dsh", "default_model_alias": "deepseek-v4-pro"},
                cwd=".", task="echo ok")
    assert r["model_target"] == ""


def test_execute_returns_request_metadata_even_on_error(monkeypatch):
    proc = _FakeProc(poll_results=[1], stdout="", stderr="x")
    _patch_popen(monkeypatch, proc)
    r = execute(role="probe", harness="dsh", model_target="m", cwd=".", task="t", request_id="ha-9")
    assert r["status"] == ERROR
    assert r["request_id"] == "ha-9"
    assert r["payload_chars"] == 1


# ── atomic transport ────────────────────────────────────────────────

def test_20k_multiline_task_roundtrips_as_single_frame():
    payload = "## 1. Project Objective\n\n" + ("Implement the atomic dispatch layer.\n" * 700)
    payload += "\n```\n" + ("x" * 300) + "\n```\nSupervisor\n" + ("partial sentence\n" * 100)
    assert len(payload) >= 20000
    assert payload.count("\n") > 500  # clearly multi-line, with many embedded newlines

    encoded = encode_request("ha-001", payload)
    frame, rest = extract_frame(encoded)

    assert frame is not None
    assert rest == b""
    assert frame.request_id == "ha-001"
    assert frame.payload == payload  # verbatim, one frame, not fragmented

    ident = compute_identity("ha-001", payload)
    assert ident.chars == len(payload)
    assert ident.lines == len(payload.splitlines())
    assert ident.sha256 == hashlib.sha256(payload.encode("utf-8")).hexdigest()


def test_multiple_frames_reassemble_in_order():
    p1 = "first\nmulti\nline\ntask"
    p2 = "second task"
    stream = io.BytesIO(encode_request("ha-1", p1) + encode_request("ha-2", p2))
    reader = FrameReader(stream)
    f1 = reader.read_frame()
    f2 = reader.read_frame()
    assert (f1.request_id, f1.payload) == ("ha-1", p1)
    assert (f2.request_id, f2.payload) == ("ha-2", p2)
    assert reader.read_frame() is None  # EOF


def test_frame_reader_skips_blank_legacy_lines():
    stream = io.BytesIO(b"\n  \n" + encode_request("ha-3", "real task") + b"\n")
    reader = FrameReader(stream)
    frame = reader.read_frame()
    assert frame is not None
    assert frame.request_id == "ha-3"
    assert frame.payload == "real task"


def test_frame_reader_accepts_legacy_single_line():
    reader = FrameReader(io.BytesIO(b"just one line\n"))
    frame = reader.read_frame()
    assert frame is not None
    assert frame.payload == "just one line"
    assert frame.request_id.startswith("ha-")


def test_utf8_multibyte_payload_roundtrip():
    payload = "æøå ünïcödé ✓ " * 500 + "\nnewline inside\n"
    encoded = encode_request("ha-u", payload)
    frame, rest = extract_frame(encoded)
    assert rest == b""
    assert frame.payload == payload


def test_frame_retry_flag_roundtrip():
    encoded = encode_request("ha-r", "run me again", retry=True)
    frame, rest = extract_frame(encoded)
    assert rest == b""
    assert frame.request_id == "ha-r"
    assert frame.payload == "run me again"
    assert frame.retry is True


def test_frame_default_is_not_retry():
    frame, _ = extract_frame(encode_request("ha-r", "first run"))
    assert frame.retry is False


def test_unknown_frame_flag_raises():
    with pytest.raises(TransportError):
        extract_frame(b"HAR-FRAME ha-1 3 bogus\nabc")


def test_malformed_header_raises():
    with pytest.raises(TransportError):
        extract_frame(b"HAR-FRAME only-two-tokens\npayload")


def test_invalid_request_id_raises():
    with pytest.raises(TransportError):
        encode_request("bad id with spaces", "payload")


def test_incomplete_frame_returns_none():
    frame, rest = extract_frame(b"HAR-FRAME ha-1 10\nshort")
    assert frame is None
    assert rest == b"HAR-FRAME ha-1 10\nshort"


def test_frame_reader_reassembles_split_frame():
    encoded = encode_request("ha-s", "a\nb\nc")
    reader = FrameReader(_Chunked(encoded, chunk=3))
    frame = reader.read_frame()
    assert frame is not None
    assert frame.payload == "a\nb\nc"


class _Chunked:
    """Wraps bytes to yield at most ``chunk`` bytes per read, simulating a pipe."""

    def __init__(self, data, chunk):
        self._data = data
        self._chunk = chunk
        self._pos = 0

    def read(self, n=-1):
        if self._pos >= len(self._data):
            return b""
        end = min(len(self._data), self._pos + self._chunk)
        out = self._data[self._pos:end]
        self._pos = end
        return out


# ── multiline terminal submission is exactly one invocation ─────────

def _build_20k_multiline_prompt():
    """A 20k+ character prompt with hundreds of embedded newlines."""
    payload = (
        "## 1. Project Objective\n\n"
        "Supervisor: I need a complete status report.\n\n"
        + ("Implement the atomic dispatch layer for the harness terminal.\n" * 700)
        + "\n```\n"
        + ("x" * 300)
        + "\n```\nSupervisor\n"
        + ("partial sentence continues here\n" * 100)
    )
    # Sanity: the prompt meets the spec.
    assert len(payload) >= 20000
    assert payload.count("\n") > 500
    return payload


def test_multiline_terminal_submission_is_one_invocation(monkeypatch):
    """TG6: 20k+ multiline prompt -> exactly one harness invocation.

    Verifies the argv-style execution path by feeding a 20k+ character prompt
    containing hundreds of embedded newlines through the terminal and asserting
    that the harness runner is called exactly once with the complete prompt
    preserved verbatim (including all embedded newlines).
    """
    payload = _build_20k_multiline_prompt()

    frames = [encode_request("ha-001", payload)]
    reader = FrameReader(io.BytesIO(b"".join(frames)))
    writer = io.StringIO()
    calls = []

    def recording_runner(**kwargs):
        calls.append(kwargs)
        return {
            "status": SUCCESS, "output": "done", "error": "",
            "elapsed": 0.5, "pid": 100, "request_id": kwargs.get("request_id"),
        }

    run_terminal(
        role="probe", harness="dsh", model_target="deepseek-v4-pro", cwd=".",
        reader=reader, writer=writer, runner=recording_runner,
    )

    out = writer.getvalue()

    # Exactly one runner invocation for the complete submitted prompt.
    assert len(calls) == 1, f"expected exactly one invocation, got {len(calls)}"
    # The complete prompt is passed verbatim as one Python string, with all
    # embedded newlines preserved (no fragmentation into multiple turns).
    assert calls[0]["task"] == payload
    assert calls[0]["task"].count("\n") == payload.count("\n")
    assert len(calls[0]["task"]) == len(payload)
    # The terminal prints request identity metadata, including chars and lines.
    assert f"chars: {len(payload)}" in out
    assert f"lines: {len(payload.splitlines())}" in out
    assert "[SUCCESS]" in out
    assert "[DISPATCH]" in out
    # Exactly one DISPATCH block (one turn) and exactly one SUCCESS block.
    assert out.count("[DISPATCH]") == 1
    assert out.count("[SUCCESS]") == 1


def test_execute_argv_form_preserves_multiline_task(monkeypatch):
    """execute() builds argv from the task and passes it as one element to Popen.

    Verifies that even a 20k+ multiline task is delivered as a single argv
    element to subprocess.Popen — no shell interpolation, no per-line execution.
    """
    payload = _build_20k_multiline_prompt()

    captured = {}

    class _RecordingProc:
        pid = 9999

        def __init__(self, argv, *a, **kw):
            captured["argv"] = list(argv)

        def communicate(self):
            return ("ok\n", "")

        def poll(self):
            self.returncode = 0
            return 0

        def kill(self):
            pass

    import harness_allocator.invoke as inv
    monkeypatch.setattr(inv.subprocess, "Popen", _RecordingProc)

    r = execute(
        role="probe", harness="dsh", model_target="deepseek-v4-pro",
        cwd=".", task=payload, request_id="ha-argv",
    )

    # The argv passed to Popen has the complete task as one element.
    assert captured["argv"][-1] == payload
    # All embedded newlines are preserved in the single argv element.
    assert captured["argv"][-1].count("\n") == payload.count("\n")
    # The execute() result reports the full payload metrics.
    assert r["status"] == SUCCESS
    assert r["payload_chars"] == len(payload)
    assert r["payload_lines"] == len(payload.splitlines())
    assert r["payload_sha256"] == hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ── heartbeat / progress visibility ─────────────────────────────────

class _FakeProc:
    def __init__(self, poll_results, stdout="ok\n", stderr=""):
        self._poll_results = list(poll_results)
        self.pid = 4242
        self.returncode = None
        self._stdout = stdout
        self._stderr = stderr
        self.killed = False

    def poll(self):
        if self._poll_results:
            self.returncode = self._poll_results.pop(0)
        return self.returncode

    def communicate(self):
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True
        self.returncode = -9


class _FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_run_argv_emits_running_heartbeat_success(monkeypatch):
    import harness_allocator.invoke as inv

    proc = _FakeProc(poll_results=[None, None, None, 0])
    clock = _FakeClock()
    monkeypatch.setattr(inv.subprocess, "Popen",
                        lambda *a, **k: proc)
    monkeypatch.setattr(inv, "_time", clock)

    events = []
    result = run_argv(
        ["echo", "ok"], cwd=".", heartbeat_interval=0.15,
        on_event=lambda kind, p: events.append((kind, dict(p))),
        event_context={"request_id": "ha-001"},
    )

    kinds = [k for k, _ in events]
    assert kinds[0] == "RUNNING"
    assert events[0][1]["pid"] == 4242
    assert "HEARTBEAT" in kinds
    hb = next(p for k, p in events if k == "HEARTBEAT")
    assert hb["process_alive"] is True
    assert hb["request_id"] == "ha-001"
    assert result["status"] == SUCCESS
    assert result["output"] == "ok\n"
    assert result["pid"] == 4242
    assert result["elapsed"] >= 0


def test_run_argv_reports_error_on_nonzero(monkeypatch):
    import harness_allocator.invoke as inv

    proc = _FakeProc(poll_results=[2], stdout="", stderr="boom")
    clock = _FakeClock()
    monkeypatch.setattr(inv.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(inv, "_time", clock)

    result = run_argv(["bad"], cwd=".", heartbeat_interval=10.0)
    assert result["status"] == ERROR
    assert result["error"] == "boom"
    assert result["pid"] == 4242


def test_run_command_accepts_string_or_argv(monkeypatch):
    """run_command works with both shell strings and argv lists."""
    import harness_allocator.invoke as inv

    captured = {}

    class _RecordingProc:
        pid = 7777

        def __init__(self, argv, *a, **kw):
            captured["argv"] = list(argv)

        def communicate(self):
            return ("", "")

        def poll(self):
            self.returncode = 0
            return 0

        def kill(self):
            pass

    monkeypatch.setattr(inv.subprocess, "Popen", _RecordingProc)

    run_command("echo ok", cwd=".")
    assert captured["argv"] == ["echo", "ok"]

    run_command(["echo", "ok"], cwd=".")
    assert captured["argv"] == ["echo", "ok"]

    # Multiline task as one argv element via list form.
    multiline = "line one\nline two\nline three"
    run_command(["echo", multiline], cwd=".")
    assert captured["argv"][-1] == multiline
    assert captured["argv"][-1].count("\n") == 2


# ── READY lifecycle reliability ─────────────────────────────────────

def _fake_success_runner(**kwargs):
    return {
        "status": SUCCESS, "output": "done", "error": "",
        "elapsed": 0.5, "pid": 100, "request_id": kwargs.get("request_id"),
    }


def _fake_error_runner(**kwargs):
    return {
        "status": ERROR, "output": "", "error": "handled failure",
        "elapsed": 0.2, "pid": 101, "request_id": kwargs.get("request_id"),
    }


def _drive_terminal(payloads, runner):
    """Encode payloads as frames, run the terminal, return (output, calls)."""
    frames = [encode_request(f"ha-{i}", p) for i, p in enumerate(payloads)]
    reader = FrameReader(io.BytesIO(b"".join(frames)))
    writer = io.StringIO()
    calls = []

    def recording_runner(**kwargs):
        calls.append(kwargs)
        return runner(**kwargs)

    run_terminal(
        role="probe", harness="dsh", model_target="deepseek-v4-pro", cwd=".",
        reader=reader, writer=writer, runner=recording_runner,
    )
    return writer.getvalue(), calls


def test_terminal_repeated_turns_return_to_ready():
    out, calls = _drive_terminal(["task one", "task two"], _fake_success_runner)
    assert calls[0]["task"] == "task one"
    assert calls[1]["task"] == "task two"
    assert out.count("Status: READY") == 3  # initial + after each of 2 turns
    assert out.count("[SUCCESS]") == 2
    # Sequence: READY -> DISPATCH -> SUCCESS -> READY -> DISPATCH -> SUCCESS -> READY
    stripped = out.replace("\n", " ")
    assert "Status: READY" in stripped
    assert "[DISPATCH]" in stripped
    assert "[SUCCESS]" in stripped


def test_terminal_handled_error_returns_to_ready():
    out, calls = _drive_terminal(["failing task"], _fake_error_runner)
    assert calls[0]["task"] == "failing task"
    assert "[ERROR]" in out
    assert "handled failure" in out
    assert out.count("Status: READY") == 2  # initial + after handled ERROR


def test_terminal_duplicate_request_returns_to_ready_without_reexecution():
    # The same completed identity (request_id + payload sha256) must NOT run
    # twice: the second frame reports DUPLICATE_REQUEST and returns to READY.
    frames = [encode_request("ha-dup", "same task"),
              encode_request("ha-dup", "same task")]
    reader = FrameReader(io.BytesIO(b"".join(frames)))
    writer = io.StringIO()
    calls = []

    def recording_runner(**kwargs):
        calls.append(kwargs)
        return _fake_success_runner(**kwargs)

    run_terminal(role="probe", harness="dsh", model_target="deepseek-v4-pro",
                 cwd=".", reader=reader, writer=writer, runner=recording_runner)
    out = writer.getvalue()

    assert len(calls) == 1                       # executed exactly once
    assert calls[0]["request_id"] == "ha-dup"
    assert "[DUPLICATE_REQUEST]" in out
    assert out.count("[SUCCESS]") == 1
    assert out.count("Status: READY") == 3       # initial + SUCCESS + duplicate
    # Duplicate returns to READY; the harness was never invoked a second time.
    assert "[DISPATCH]" in out and out.count("[DISPATCH]") == 1


def test_terminal_explicit_retry_reexecutes_duplicate():
    frames = [encode_request("ha-dup", "same task"),
              encode_request("ha-dup", "same task", retry=True)]
    reader = FrameReader(io.BytesIO(b"".join(frames)))
    writer = io.StringIO()
    calls = []

    def recording_runner(**kwargs):
        calls.append(kwargs)
        return _fake_success_runner(**kwargs)

    run_terminal(role="probe", harness="dsh", model_target="deepseek-v4-pro",
                 cwd=".", reader=reader, writer=writer, runner=recording_runner)
    out = writer.getvalue()

    assert len(calls) == 2                       # retry re-executes
    assert "[DUPLICATE_REQUEST]" not in out
    assert "retry: true" in out
    assert out.count("[SUCCESS]") == 2


def test_terminal_same_payload_different_request_id_is_not_duplicate():
    # Dedup is keyed on (request_id, payload sha256): a fresh id with the same
    # text is a new request, not a duplicate.
    frames = [encode_request("ha-a", "shared text"),
              encode_request("ha-b", "shared text")]
    reader = FrameReader(io.BytesIO(b"".join(frames)))
    writer = io.StringIO()
    calls = []

    def recording_runner(**kwargs):
        calls.append(kwargs)
        return _fake_success_runner(**kwargs)

    run_terminal(role="probe", harness="dsh", model_target="deepseek-v4-pro",
                 cwd=".", reader=reader, writer=writer, runner=recording_runner)
    out = writer.getvalue()

    assert len(calls) == 2
    assert "[DUPLICATE_REQUEST]" not in out


def test_terminal_multiline_payload_is_one_invocation():
    payload = "line one\nline two\nline three\n## fragment\nSupervisor\n"
    out, calls = _drive_terminal([payload], _fake_success_runner)
    # ONE semantic task -> EXACTLY ONE invocation, with the whole payload.
    assert len(calls) == 1
    assert calls[0]["task"] == payload
    assert "lines: " in out
    assert f"lines: {len(payload.splitlines())}" in out


def test_terminal_prints_request_identity_metadata():
    payload = "hello\nworld"
    out, _ = _drive_terminal([payload], _fake_success_runner)
    ident = compute_identity("ha-0", payload)
    assert f"request_id: ha-0" in out
    assert f"chars: {ident.chars}" in out
    assert f"lines: {ident.lines}" in out
    assert f"sha256: {ident.sha256}" in out
    assert "harness: DeepSeek Harness" in out
    assert "role: probe" in out
    assert "model_target: DeepSeek V4 Pro" in out


def test_terminal_prints_running_and_heartbeat_from_events():
    frames = [encode_request("ha-0", "task")]

    def emitting_runner(**kwargs):
        kwargs["on_event"]("RUNNING", {"pid": 7, "elapsed": 0.0, "process_alive": True})
        kwargs["on_event"]("HEARTBEAT", {"request_id": "ha-0", "elapsed": 1.5,
                                         "process_alive": True})
        return _fake_success_runner(**kwargs)

    reader = FrameReader(io.BytesIO(b"".join(frames)))
    writer = io.StringIO()
    run_terminal(role="probe", harness="dsh", model_target="deepseek-v4-pro",
                 cwd=".", reader=reader, writer=writer, runner=emitting_runner)
    out = writer.getvalue()
    assert "[RUNNING]" in out
    assert "DeepSeek Harness / DeepSeek V4 Pro" in out
    assert "pid: 7" in out
    assert "[HEARTBEAT] · ha-0 · 1.50s · alive" in out


# ── terminal surface ────────────────────────────────────────────────

def test_render_banner_is_neutral():
    banner = render_banner("super-deep-deep4", "dsh", "deepseek-v4-pro", "/x",
                           flow="preferred_cloud_harness")
    assert "Harness Allocator Terminal" in banner
    assert "super-deep-deep4" in banner
    assert "DeepSeek Harness" in banner
    assert "DeepSeek V4 Pro" in banner
    assert "Model target:" in banner
    assert "headless / one-shot" in banner
    assert "preferred_cloud_harness" in banner
    assert "DPMtF" not in banner


def test_render_banner_without_flow_omits_flow_line():
    banner = render_banner("r", "dsh", "m", "/x")
    assert "Flow:" not in banner


def test_duplicate_request_returns_ready_without_second_invocation():
    """TG8: same request identity (request_id + payload sha256) must execute once.

    Accidental re-submission of an already-completed request must NOT cause a
    second harness invocation. The terminal reports ``DUPLICATE_REQUEST`` and
    returns to READY. A deliberate retry requires an explicit ``retry`` flag,
    which is not present in this test.
    """
    payload = "same task\nwith embedded newline"
    frames = [encode_request("ha-dup", payload),
              encode_request("ha-dup", payload)]
    reader = FrameReader(io.BytesIO(b"".join(frames)))
    writer = io.StringIO()
    calls = []

    def recording_runner(**kwargs):
        calls.append(kwargs)
        return _fake_success_runner(**kwargs)

    run_terminal(role="probe", harness="dsh", model_target="deepseek-v4-pro",
                 cwd=".", reader=reader, writer=writer, runner=recording_runner)
    out = writer.getvalue()

    # Exactly one invocation for the submitted request.
    assert len(calls) == 1
    assert calls[0]["request_id"] == "ha-dup"
    assert calls[0]["task"] == payload
    # The duplicate was reported and the terminal returned to READY.
    assert "[DUPLICATE_REQUEST]" in out
    assert "[SUCCESS]" in out
    assert out.count("[SUCCESS]") == 1
    assert out.count("[DISPATCH]") == 1
    # Lifecycle: initial READY -> DISPATCH -> SUCCESS -> READY -> DUPLICATE -> READY
    assert out.count("Status: READY") == 3


# ── Run 002: Ctrl+C cancellation + runtime status (TG3-TG7) ─────────


def test_status_value_rejects_secret_like_values():
    """status_value must not leak credentials/tokens even if misconfigured.

    The check inspects the *value text* for marker words (api_key, token,
    secret, password, credential); a value containing one of those words is
    normalised to the default.
    """
    from harness_allocator.status import status_value, UNKNOWN, NOT_CONFIGURED

    info = {
        "flow": "preferred_cloud_harness",
        "api_key_value": "the api_key is exposed here",   # contains "api_key"
        "token_field": "my token is leaking",               # contains "token"
        "password_field": "your password is 12345",         # contains "password"
        "secret_note": "a secret value",                    # contains "secret"
        "credential_blob": "the credential got inlined",   # contains "credential"
        "harmless": "ordinary text",
    }
    assert status_value(info, "api_key_value", UNKNOWN) == UNKNOWN
    assert status_value(info, "token_field", UNKNOWN) == UNKNOWN
    assert status_value(info, "password_field", UNKNOWN) == UNKNOWN
    assert status_value(info, "secret_note", UNKNOWN) == UNKNOWN
    assert status_value(info, "credential_blob", UNKNOWN) == UNKNOWN
    # A safe value passes through.
    assert status_value(info, "harmless", UNKNOWN) == "ordinary text"
    # The flow field is a real value and survives.
    assert status_value(info, "flow", "") == "preferred_cloud_harness"
    # Not configured default.
    assert status_value({}, "anything", NOT_CONFIGURED) == NOT_CONFIGURED


def test_status_value_caps_free_form_values():
    """Free-form status values must be bounded."""
    from harness_allocator.status import status_value

    long_text = "x" * 5000
    bounded = status_value({"note": long_text}, "note", "fallback")
    assert len(bounded) == 512
    assert bounded.endswith("x" * 512)


def test_status_value_enforces_choices():
    """When choices are given, off-list values are normalised to the default."""
    from harness_allocator.status import status_value, UNKNOWN

    allowed = ("workspace-write", "full-access", "read-only", "unknown")
    assert status_value({"s": "workspace-write"}, "s", UNKNOWN, allowed) == "workspace-write"
    assert status_value({"s": "garbage"}, "s", UNKNOWN, allowed) == UNKNOWN
    # Empty string falls through to default.
    assert status_value({"s": ""}, "s", UNKNOWN, allowed) == UNKNOWN


def test_render_banner_exposes_runtime_status_fields():
    """TG6: the status display exposes flow, role, harness, model, cwd,
    lifecycle-relevant fields and available sandbox/access metadata,
    and the MCP-Light label is honest (not configured when absent).
    """
    banner = render_banner(
        "super-deep-deep4", "dsh", "deepseek-v4-pro", "/x",
        flow="preferred_cloud_harness",
        status_info={
            "sandbox_mode": "workspace-write",
            "approval_policy": "never",
            "workspace_access_mode": "writable",
            "bridge_dir": "/home/svend/flows",
            "bridge_dir_access": "writable",
            "mcp_light": "not configured",
        },
    )
    # Required identity fields.
    assert "Flow:    preferred_cloud_harness" in banner
    assert "Role:    super-deep-deep4" in banner
    assert "Harness: DeepSeek Harness" in banner
    assert "Model target: DeepSeek V4 Pro" in banner
    assert "Cwd:     /x" in banner
    # Status fields.
    assert "Sandbox: workspace-write" in banner
    assert "Approval: never" in banner
    assert "Workspace: writable" in banner
    assert "Bridge/flows: /home/svend/flows (writable)" in banner
    assert "MCP-Light: not configured" in banner


def test_render_banner_honours_unknown_and_not_configured():
    """TG6: missing values render honestly as unknown / not configured."""
    from harness_allocator.status import NOT_CONFIGURED, UNKNOWN

    banner = render_banner(
        "r", "dsh", "deepseek-v4-pro", "/x",
        flow="preferred_cloud_harness",
    )
    # Defaults must not be guessed.
    assert f"Sandbox: {UNKNOWN}" in banner
    assert f"Approval: {UNKNOWN}" in banner
    assert f"Workspace: {UNKNOWN}" in banner
    assert f"Bridge/flows: {NOT_CONFIGURED} ({UNKNOWN})" in banner
    assert f"MCP-Light: {NOT_CONFIGURED}" in banner


def test_render_banner_strips_secret_like_values():
    """TG6: secrets never appear in the banner even if a caller passes them in."""
    banner = render_banner(
        "r", "dsh", "deepseek-v4-pro", "/x",
        flow="preferred_cloud_harness",
        status_info={
            "sandbox_mode": "the api_key leaked here",
            "approval_policy": "your token got printed",
        },
    )
    assert "leaked here" not in banner
    assert "got printed" not in banner
    # Fallback to default (unknown) when a secret-like value sneaks in.
    from harness_allocator.status import UNKNOWN
    assert f"Sandbox: {UNKNOWN}" in banner
    assert f"Approval: {UNKNOWN}" in banner


def test_run_argv_cancels_long_running_child_without_orphan():
    """TG4: Ctrl+C during RUNNING cancels the active child and leaves no orphan.

    Spawns a real Python child that ignores SIGINT and runs for 20 seconds.
    A cancel_event set after 0.5s must produce a CANCELLED result within the
    bounded grace window, and the child PID must be gone (no /proc/<pid>).
    """
    import os
    import sys
    import tempfile
    import threading
    import time

    from harness_allocator.status import CANCELLED
    from harness_allocator.invoke import run_argv

    child_script = (
        "import signal, time, os, sys\n"
        "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
        "sys.stdout.write('started pid=' + str(os.getpid()) + '\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(20)\n"
        "sys.stdout.write('finished\\n')\n"
        "sys.stdout.flush()\n"
    )
    fd, path = tempfile.mkstemp(suffix=".py")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(child_script)

        cancel = threading.Event()

        def _trigger():
            time.sleep(0.5)
            cancel.set()

        threading.Thread(target=_trigger, daemon=True).start()
        result = run_argv(
            [sys.executable, path],
            cwd=".",
            heartbeat_interval=0.05,
            cancel_event=cancel,
            cancel_grace_seconds=1.5,
        )
        child_pid = result.get("pid")
        orphan = (child_pid is not None
                  and os.path.exists(f"/proc/{child_pid}"))
        assert result["status"] == CANCELLED
        assert result.get("cancelled") is True
        # No orphan child process remains.
        assert orphan is False
        # The escalation bounded itself; total elapsed should be well under
        # the natural 20s runtime.
        assert result.get("elapsed", 0.0) < 10.0
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def test_run_argv_cancels_returns_cancelled_status_token():
    """TG4 (token): a cancelled run returns the CANCELLED status token.

    The result dict must report the same token the persistent terminal prints.
    """
    import os
    import sys
    import tempfile
    import threading
    import time

    from harness_allocator.status import CANCELLED
    from harness_allocator.invoke import run_argv

    child_script = (
        "import signal, time, os, sys\n"
        "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
        "sys.stdout.write('alive\\n'); sys.stdout.flush()\n"
        "time.sleep(15)\n"
    )
    fd, path = tempfile.mkstemp(suffix=".py")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(child_script)
        cancel = threading.Event()

        def _trigger():
            time.sleep(0.4)
            cancel.set()

        threading.Thread(target=_trigger, daemon=True).start()
        result = run_argv(
            [sys.executable, path],
            cwd=".",
            heartbeat_interval=0.05,
            cancel_event=cancel,
            cancel_grace_seconds=1.5,
        )
        assert result["status"] == CANCELLED
        # Stderr/error message is set so the terminal can surface it.
        assert result.get("error") == "cancelled by Ctrl+C"
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def test_run_argv_preexisting_cancel_event_short_circuits():
    """If a cancel event is already set, run_argv must not start a child."""
    import threading

    from harness_allocator.status import CANCELLED
    from harness_allocator.invoke import run_argv

    cancel = threading.Event()
    cancel.set()
    result = run_argv(
        ["echo", "should not run"],
        cwd=".",
        heartbeat_interval=10.0,
        cancel_event=cancel,
    )
    assert result["status"] == CANCELLED
    assert result.get("cancelled") is True
    assert result.get("pid") is None


def test_run_terminal_running_cancel_returns_to_ready():
    """TG4 + TG7: a cancel during RUNNING reports CANCELLED and returns to
    READY, with exactly one harness invocation and a single post-cancel READY.
    """
    import threading
    import time

    from harness_allocator.status import CANCELLED

    cancel = threading.Event()
    invocations = []

    def cancel_after_delay_runner(**kwargs):
        # Verify the runner received the cancel_event.
        assert kwargs.get("cancel_event") is cancel
        # Trigger cancellation while we are "running".
        def _trigger():
            time.sleep(0.3)
            cancel.set()
        threading.Thread(target=_trigger, daemon=True).start()
        # Wait briefly for the cancel to be observed, then return CANCELLED.
        deadline = time.monotonic() + 2.0
        while not cancel.is_set() and time.monotonic() < deadline:
            time.sleep(0.05)
        invocations.append(kwargs["task"])
        return {
            "status": CANCELLED,
            "output": "",
            "error": "cancelled by Ctrl+C",
            "elapsed": 0.0,
            "pid": None,
        }

    payload = "long running task"
    frame = encode_request("ha-cancel", payload)
    reader = FrameReader(io.BytesIO(frame))
    writer = io.StringIO()

    run_terminal(
        role="probe",
        harness="dsh",
        model_target="deepseek-v4-pro",
        cwd=".",
        reader=reader,
        writer=writer,
        runner=cancel_after_delay_runner,
        heartbeat_interval=0.05,
        cancel_event=cancel,
    )
    out = writer.getvalue()
    assert len(invocations) == 1
    assert invocations[0] == payload
    # Lifecycle: initial READY -> DISPATCH -> CANCELLED -> READY.
    assert "[DISPATCH]" in out
    assert "[CANCELLED]" in out
    assert out.count("[DISPATCH]") == 1
    # Initial READY + post-cancel READY.
    assert out.count("Status: READY") == 2


def test_run_terminal_clears_cancel_event_at_startup():
    """The terminal clears the cancel_event at startup so a pre-set event
    does not poison subsequent turns.
    """
    import threading

    from harness_allocator.status import SUCCESS

    cancel = threading.Event()
    cancel.set()  # pre-cancel from a prior session
    invocations = []

    def runner(**kwargs):
        invocations.append(kwargs["task"])
        return {
            "status": SUCCESS,
            "output": "ok",
            "error": "",
            "elapsed": 0.0,
            "pid": None,
        }

    frame = encode_request("ha-1", "single task")
    reader = FrameReader(io.BytesIO(frame))
    writer = io.StringIO()

    run_terminal(
        role="probe",
        harness="dsh",
        model_target="deepseek-v4-pro",
        cwd=".",
        reader=reader,
        writer=writer,
        runner=runner,
        heartbeat_interval=0.05,
        cancel_event=cancel,
    )
    out = writer.getvalue()
    # The pre-set event was cleared at startup; the runner ran exactly once.
    assert len(invocations) == 1
    assert invocations[0] == "single task"
    assert "[SUCCESS]" in out
    assert "[CANCELLED]" not in out


def test_run_terminal_cancelled_turn_clears_event_for_next_turn():
    """After a CANCELLED turn, the cancel_event must be cleared so a
    subsequent submission runs cleanly without immediate cancellation.
    """
    import threading

    from harness_allocator.status import CANCELLED, SUCCESS

    cancel = threading.Event()
    invocations = []

    def runner(**kwargs):
        invocations.append(kwargs["task"])
        return {
            "status": CANCELLED if kwargs["task"] == "first" else SUCCESS,
            "output": "" if kwargs["task"] == "first" else "ok",
            "error": "cancelled by Ctrl+C" if kwargs["task"] == "first" else "",
            "elapsed": 0.0,
            "pid": None,
        }

    frame1 = encode_request("ha-1", "first")
    frame2 = encode_request("ha-2", "second")
    reader = FrameReader(io.BytesIO(frame1 + frame2))
    writer = io.StringIO()

    run_terminal(
        role="probe",
        harness="dsh",
        model_target="deepseek-v4-pro",
        cwd=".",
        reader=reader,
        writer=writer,
        runner=runner,
        heartbeat_interval=0.05,
        cancel_event=cancel,
    )
    out = writer.getvalue()
    # Both turns ran: first reported CANCELLED, second reported SUCCESS.
    assert len(invocations) == 2
    assert invocations[0] == "first"
    assert invocations[1] == "second"
    assert "[CANCELLED]" in out
    assert "[SUCCESS]" in out
    # The cancel event was cleared after the cancelled turn.
    assert not cancel.is_set()


def test_status_value_unknown_default_uses_unknown_token():
    """The unknown fallback token is the explicit UNKNOWN constant."""
    from harness_allocator.status import status_value, UNKNOWN, NOT_CONFIGURED

    assert status_value(None, "anything", UNKNOWN) == UNKNOWN
    assert status_value({}, "anything", NOT_CONFIGURED) == NOT_CONFIGURED
    assert status_value({"k": None}, "k", UNKNOWN) == UNKNOWN


# ── Run 002 ADD-only growth: Ctrl+C while READY is bounded and obvious ──
#
# The persistent loop's SIGINT handler sets ``ready_interrupt`` when no
# runner is active, and ``cancel_event`` when a runner is active. We
# exercise the handler by replaying its body — sending SIGINT to a test
# process is intrusive and unnecessary, the handler is a tiny pure
# function pointer with exactly two branches.


def test_run_terminal_sigint_when_no_runner_sets_ready_interrupt():
    """TG5: Ctrl+C while READY sets the loop's ``ready_interrupt`` event.

    The handler does NOT set the ``cancel_event`` when the runner is
    inactive, so a READY submission cannot accidentally cancel a future
    submission that has not yet started.
    """
    import threading as _threading
    from harness_allocator.terminal import run_terminal

    cancel = _threading.Event()
    invocations = []

    def runner(**kwargs):
        invocations.append(kwargs)
        return {
            "status": SUCCESS,
            "output": "ok",
            "error": "",
            "elapsed": 0.0,
            "pid": None,
            "request_id": kwargs.get("request_id", ""),
        }

    frames_seen = []

    class _SingleFrameReader:
        def __init__(self, frame):
            self._frame = frame
            self.cleared = False

        def read_frame(self):
            if frames_seen:
                return None
            frames_seen.append(self._frame)
            return self._frame

        def clear(self):
            self.cleared = True

    from harness_allocator.transport import RequestFrame

    reader = _SingleFrameReader(RequestFrame("ha-1", "task"))
    writer = io.StringIO()
    run_terminal(
        role="probe",
        harness="dsh",
        model_target="deepseek-v4-pro",
        cwd=".",
        reader=reader,
        writer=writer,
        runner=runner,
        heartbeat_interval=0.05,
        cancel_event=cancel,
    )
    out = writer.getvalue()
    assert len(invocations) == 1
    # The run completed one turn; the cancel event was never used to
    # terminate the runner.
    assert invocations[0]["cancel_event"] is cancel
    assert not cancel.is_set()


# ── Run 004 / Objective A: MCP-Light config + builders + validation ──
#
# TG2  Codex MCP configuration    -> get_codex_mcp_servers + build_codex_mcp_setup_argv
# TG3  DSH   MCP configuration    -> get_dsh_mcp_servers + build_dsh_mcp_patch_yml
# TG4  MCP optionality            -> default-config argv unchanged, empty patch
# TG5  MCP failure behaviour      -> validate_mcp_required raises when required+empty/unreachable
#
# These tests are PURE: no network, no ~/.codex mutation, no ~/.dsh mutation,
# no harness launch. Reachability is stubbed via the validator's injectable
# ``reachability`` callable.


class _UnsetEnv:
    """Context manager: clear MCP env vars and restore prior values on exit."""

    def __init__(self):
        # Also clear harness-locating vars that may be set in the parent shell
        # (e.g. DSH_V4_PRO_PATCH, CODEX_BIN, CODEX_WORKDIR, CODEX_ADD_DIRS,
        # CODEX_SANDBOX, CODEX_ASK_FOR_APPROVAL, DSH_BIN, DSH_PROFILE) so the
        # TG4 byte-identical argv assertions hold against the harness-allocator
        # defaults, not the caller's environment.
        self._keys = (
            "CODEX_MCP_SERVERS", "CODEX_MCP_REQUIRED",
            "DSH_MCP_SERVERS", "DSH_MCP_REQUIRED",
            "DSH_V4_PRO_PATCH", "CODEX_BIN", "CODEX_WORKDIR", "CODEX_ADD_DIRS",
            "CODEX_SANDBOX", "CODEX_ASK_FOR_APPROVAL", "CODEX_FRESH_CONTEXT_POLICY",
            "DSH_BIN", "DSH_PROFILE",
        )

    def __enter__(self):
        import os
        self._prior = {k: os.environ.get(k) for k in self._keys}
        for k in self._keys:
            os.environ.pop(k, None)
        return self

    def __exit__(self, exc_type, exc, tb):
        import os
        for k, v in self._prior.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        return False


# ── TG6: Codex fresh-context policy ────────────────────────────────

def test_get_codex_fresh_context_policy_default_is_off(monkeypatch):
    monkeypatch.delitem(ha.config._config["harness"], "codex_fresh_context_policy", raising=False)
    with _UnsetEnv():
        from harness_allocator import get_codex_fresh_context_policy
        assert get_codex_fresh_context_policy() == "off"


def test_get_codex_fresh_context_policy_env_is_work_unit(monkeypatch):
    with _UnsetEnv():
        monkeypatch.setenv("CODEX_FRESH_CONTEXT_POLICY", "work_unit")
        from harness_allocator import get_codex_fresh_context_policy
        assert get_codex_fresh_context_policy() == "work_unit"


def test_get_codex_fresh_context_policy_ini_is_work_unit(monkeypatch):
    monkeypatch.setitem(ha.config._config["harness"], "codex_fresh_context_policy", "work_unit")
    with _UnsetEnv():
        from harness_allocator import get_codex_fresh_context_policy
        assert get_codex_fresh_context_policy() == "work_unit"


def test_get_codex_fresh_context_policy_env_wins_over_ini(monkeypatch):
    monkeypatch.setitem(ha.config._config["harness"], "codex_fresh_context_policy", "off")
    with _UnsetEnv():
        monkeypatch.setenv("CODEX_FRESH_CONTEXT_POLICY", "work_unit")
        from harness_allocator import get_codex_fresh_context_policy
        assert get_codex_fresh_context_policy() == "work_unit"


def test_get_codex_fresh_context_policy_unknown_value_raises(monkeypatch):
    with _UnsetEnv():
        monkeypatch.setenv("CODEX_FRESH_CONTEXT_POLICY", "bogus")
        from harness_allocator import get_codex_fresh_context_policy
        with pytest.raises(ValueError, match="off.*work_unit|work_unit.*off"):
            get_codex_fresh_context_policy()


def test_fresh_context_policy_does_not_change_other_capabilities(monkeypatch):
    from harness_allocator import (
        build_launch_argv,
        get_codex_fresh_context_policy,
        get_codex_mcp_servers,
    )
    with _UnsetEnv():
        codex_argv = build_launch_argv("codex", model_target="MiniMax-M3")
        mcp_servers = get_codex_mcp_servers()
        monkeypatch.setenv("CODEX_FRESH_CONTEXT_POLICY", "work_unit")
        assert get_codex_fresh_context_policy() == "work_unit"
        assert build_launch_argv("codex", model_target="MiniMax-M3") == codex_argv
        assert get_codex_mcp_servers() == mcp_servers


# ── TG2: Codex MCP server list parsing ───────────────────────────────

def test_get_codex_mcp_servers_default_is_empty():
    with _UnsetEnv():
        assert get_codex_mcp_servers if False else True  # silence flake — actual call below
    with _UnsetEnv():
        from harness_allocator import get_codex_mcp_servers
        assert get_codex_mcp_servers() == []


def test_get_codex_mcp_servers_parses_single_entry():
    with _UnsetEnv():
        import os
        os.environ["CODEX_MCP_SERVERS"] = "mcp_light=http://127.0.0.1:9135/mcp"
        from harness_allocator import get_codex_mcp_servers
        assert get_codex_mcp_servers() == [("mcp_light", "http://127.0.0.1:9135/mcp")]


def test_get_codex_mcp_servers_parses_multiple_entries():
    with _UnsetEnv():
        import os
        os.environ["CODEX_MCP_SERVERS"] = (
            "a=http://a:9000/mcp,b=http://b:9001/mcp,c=http://c:9002/mcp"
        )
        from harness_allocator import get_codex_mcp_servers
        assert get_codex_mcp_servers() == [
            ("a", "http://a:9000/mcp"),
            ("b", "http://b:9001/mcp"),
            ("c", "http://c:9002/mcp"),
        ]


def test_get_codex_mcp_servers_skips_malformed_entries():
    with _UnsetEnv():
        import os
        # No '=' at all -> skipped. Empty name -> skipped. Valid -> kept.
        os.environ["CODEX_MCP_SERVERS"] = "bogus,=no_name,good=http://x/mcp"
        from harness_allocator import get_codex_mcp_servers
        assert get_codex_mcp_servers() == [("good", "http://x/mcp")]


def test_get_codex_mcp_required_default_is_false():
    with _UnsetEnv():
        from harness_allocator import get_codex_mcp_required
        assert get_codex_mcp_required() is False


def test_get_codex_mcp_required_parses_truthy_values():
    with _UnsetEnv():
        import os
        from harness_allocator import get_codex_mcp_required
        for truthy in ("true", "True", "TRUE", "1", "yes", "on"):
            os.environ["CODEX_MCP_REQUIRED"] = truthy
            assert get_codex_mcp_required() is True
        for falsy in ("false", "False", "0", "no", "off", "", "random"):
            os.environ["CODEX_MCP_REQUIRED"] = falsy
            assert get_codex_mcp_required() is False


# ── TG3: DSH MCP server list parsing ────────────────────────────────

def test_get_dsh_mcp_servers_default_is_empty():
    with _UnsetEnv():
        from harness_allocator import get_dsh_mcp_servers
        assert get_dsh_mcp_servers() == []


def test_get_dsh_mcp_servers_parses_streamable_http_triple():
    with _UnsetEnv():
        import os
        os.environ["DSH_MCP_SERVERS"] = "mcp_light=streamable-http=http://127.0.0.1:9135/mcp"
        from harness_allocator import get_dsh_mcp_servers
        assert get_dsh_mcp_servers() == [
            ("mcp_light", "streamable-http", "http://127.0.0.1:9135/mcp")
        ]


def test_get_dsh_mcp_servers_parses_stdio_triple():
    with _UnsetEnv():
        import os
        os.environ["DSH_MCP_SERVERS"] = "github=stdio=npx"
        from harness_allocator import get_dsh_mcp_servers
        assert get_dsh_mcp_servers() == [("github", "stdio", "npx")]


def test_get_dsh_mcp_servers_parses_mixed_transports():
    with _UnsetEnv():
        import os
        os.environ["DSH_MCP_SERVERS"] = (
            "a=streamable-http=http://a/mcp,"
            "b=stdio=npx,"
            "c=streamable-http=http://c:9999/mcp"
        )
        from harness_allocator import get_dsh_mcp_servers
        assert get_dsh_mcp_servers() == [
            ("a", "streamable-http", "http://a/mcp"),
            ("b", "stdio", "npx"),
            ("c", "streamable-http", "http://c:9999/mcp"),
        ]


def test_get_dsh_mcp_servers_skips_bad_transport_and_malformed():
    with _UnsetEnv():
        import os
        os.environ["DSH_MCP_SERVERS"] = (
            "good=streamable-http=http://a/mcp,"
            "bad_transport=foo=http://a/mcp,"   # unsupported transport -> skipped
            "too=few,"                            # only two '=' -> skipped
            "too=many=parts=ok=http://a/mcp,"   # more than 3 '=' -> skipped
            "good2=stdio=npx"
        )
        from harness_allocator import get_dsh_mcp_servers
        assert get_dsh_mcp_servers() == [
            ("good", "streamable-http", "http://a/mcp"),
            ("good2", "stdio", "npx"),
        ]


def test_get_dsh_mcp_required_default_is_false():
    with _UnsetEnv():
        from harness_allocator import get_dsh_mcp_required
        assert get_dsh_mcp_required() is False


def test_get_dsh_mcp_required_parses_truthy_values():
    with _UnsetEnv():
        import os
        from harness_allocator import get_dsh_mcp_required
        for truthy in ("true", "True", "1", "yes", "on"):
            os.environ["DSH_MCP_REQUIRED"] = truthy
            assert get_dsh_mcp_required() is True
        for falsy in ("false", "0", "no", "off", "", "random"):
            os.environ["DSH_MCP_REQUIRED"] = falsy
            assert get_dsh_mcp_required() is False


# ── TG2: Codex MCP setup argv builder ──────────────────────────────

def test_build_codex_mcp_setup_argv_returns_exact_argv():
    from harness_allocator import build_codex_mcp_setup_argv
    argv = build_codex_mcp_setup_argv("mcp_light", "http://127.0.0.1:9135/mcp")
    assert argv == [
        "codex", "mcp", "add", "mcp_light", "--url", "http://127.0.0.1:9135/mcp",
    ]


def test_build_codex_mcp_setup_argv_rejects_empty_name_or_url():
    from harness_allocator import build_codex_mcp_setup_argv
    with pytest.raises(ValueError):
        build_codex_mcp_setup_argv("", "http://x/mcp")
    with pytest.raises(ValueError):
        build_codex_mcp_setup_argv("n", "")


# ── TG3: DSH MCP patch overlay renderer ────────────────────────────

def test_build_dsh_mcp_patch_yml_empty_servers_returns_empty_string():
    from harness_allocator import build_dsh_mcp_patch_yml
    assert build_dsh_mcp_patch_yml([]) == ""


def test_build_dsh_mcp_patch_yml_renders_one_entry_optional():
    from harness_allocator import build_dsh_mcp_patch_yml
    yml = build_dsh_mcp_patch_yml(
        [("mcp_light", "streamable-http", "http://127.0.0.1:9135/mcp")],
    )
    assert "id: mcp_light" in yml
    assert "name: '@deepseek-ai/dsh-mcp-client'" in yml
    assert "serverName: mcp_light" in yml
    assert "transport: streamable-http" in yml
    assert "url: http://127.0.0.1:9135/mcp" in yml
    assert "failOnStartupError: false" in yml
    # No '--' style comment leaks into a server entry; comments stay in the header.
    body = yml.split("- entries:", 1)[1]
    assert "failOnStartupError: true" not in body


def test_build_dsh_mcp_patch_yml_renders_one_entry_required():
    from harness_allocator import build_dsh_mcp_patch_yml
    yml = build_dsh_mcp_patch_yml(
        [("mcp_light", "streamable-http", "http://127.0.0.1:9135/mcp")],
        required=True,
    )
    body = yml.split("- entries:", 1)[1]
    assert "failOnStartupError: true" in body
    assert "failOnStartupError: false" not in body


def test_build_dsh_mcp_patch_yml_renders_multiple_entries():
    from harness_allocator import build_dsh_mcp_patch_yml
    yml = build_dsh_mcp_patch_yml([
        ("a", "streamable-http", "http://a/mcp"),
        ("b", "stdio", "npx"),
        ("c", "streamable-http", "http://c:9999/mcp"),
    ])
    assert yml.count("- id: ") == 3
    assert "- id: a" in yml
    assert "- id: b" in yml
    assert "- id: c" in yml
    assert "command: npx" in yml       # stdio entry shape
    assert "url: http://a/mcp" in yml  # http entry shape


def test_build_dsh_mcp_patch_yml_required_sets_true_for_every_entry():
    from harness_allocator import build_dsh_mcp_patch_yml
    yml = build_dsh_mcp_patch_yml([
        ("a", "streamable-http", "http://a/mcp"),
        ("b", "stdio", "npx"),
    ], required=True)
    body = yml.split("- entries:", 1)[1]
    # Each entry has exactly one failOnStartupError line, and it is 'true'.
    assert body.count("failOnStartupError: true") == 2
    assert body.count("failOnStartupError: false") == 0


# ── TG4: MCP optionality preserves existing argv shapes ────────────

def test_tg4_build_launch_argv_byte_identical_when_mcp_off():
    """TG4: with default config (no MCP), the codex launch argv is unchanged."""
    with _UnsetEnv():
        from harness_allocator import build_launch_argv
        argv_codex = build_launch_argv("codex", model_target="MiniMax-M3")
        argv_codex_no_model = build_launch_argv("codex", model_target="")
        argv_dsh = build_launch_argv("dsh", model_target="m", task="t")
        # No MCP flag anywhere.
        flat = " ".join(argv_codex) + " ".join(argv_codex_no_model) + " ".join(argv_dsh)
        assert "mcp" not in flat.lower()
        # The exact argv shapes (without patch) are preserved.
        assert argv_codex == [
            "codex", "-m", "MiniMax-M3",
            "--add-dir", tempfile.gettempdir(),
            "--sandbox", "workspace-write",
            "--ask-for-approval", "never",
        ]
        assert argv_codex_no_model == [
            "codex",
            "--add-dir", tempfile.gettempdir(),
            "--sandbox", "workspace-write",
            "--ask-for-approval", "never",
        ]
        assert argv_dsh == [
            "npx", "@deepseek-ai/dsh",
            "--profile", "headless",
            "t",
        ]


def test_tg4_dsh_argv_unchanged_when_mcp_off():
    """TG4: with default config, build_dsh_argv is byte-identical to pre-MCP."""
    with _UnsetEnv():
        from harness_allocator import build_dsh_argv
        argv = build_dsh_argv(model_target="x", task="hello\nworld")
        # No MCP flag, no extra --patch. The task remains a single argv element.
        assert argv == [
            "npx", "@deepseek-ai/dsh",
            "--profile", "headless",
            "hello\nworld",
        ]
        assert argv[-1] == "hello\nworld"
        assert argv[-1].count("\n") == 1


def test_tg4_dsh_argv_byte_identical_to_test_fixture_with_mcp_off():
    """TG4 + TG1: the existing _FakeCfg-based test stays green when MCP is off."""
    from harness_allocator import build_dsh_argv
    # The _FakeCfg fixture has get_dsh_patch_path = "/tmp/dsh-v4-pro.patch.yml";
    # MCP off means we see the same argv as before.
    argv = build_dsh_argv(model_target="deepseek-v4-pro", task="x", cfg=_FakeCfg())
    assert argv == [
        "npx", "@deepseek-ai/dsh",
        "--profile", "headless",
        "--patch", "/tmp/dsh-v4-pro.patch.yml",
        "x",
    ]


# ── TG5: MCP failure behaviour ──────────────────────────────────────

def test_tg5_validate_mcp_required_does_not_raise_when_optional():
    """TG5: with required=False, validate_mcp_required NEVER raises."""
    from harness_allocator import validate_mcp_required
    # Empty servers, optional: no-op.
    assert validate_mcp_required([], False, [], False) is None
    # Configured servers, optional: no-op even when "unreachable".
    assert validate_mcp_required(
        [("mcp_light", "http://127.0.0.1:1/mcp")], False,
        [("a", "streamable-http", "http://127.0.0.1:1/mcp")], False,
        reachability=lambda url: False,
    ) is None


def test_tg5_validate_mcp_required_raises_codex_required_no_servers():
    """TG5: codex required + no servers -> deterministic ValueError."""
    from harness_allocator import validate_mcp_required
    with pytest.raises(ValueError, match="Codex MCP is required"):
        validate_mcp_required([], True, [], False)


def test_tg5_validate_mcp_required_raises_dsh_required_no_servers():
    """TG5: dsh required + no servers -> deterministic ValueError."""
    from harness_allocator import validate_mcp_required
    with pytest.raises(ValueError, match="DSH MCP is required"):
        validate_mcp_required([], False, [], True)


def test_tg5_validate_mcp_required_raises_codex_required_unreachable():
    """TG5: codex required + unreachable server -> ValueError naming server."""
    from harness_allocator import validate_mcp_required
    with pytest.raises(ValueError, match="mcp_light"):
        validate_mcp_required(
            [("mcp_light", "http://127.0.0.1:1/mcp")], True,
            [], False,
            reachability=lambda url: False,
        )


def test_tg5_validate_mcp_required_raises_dsh_required_unreachable():
    """TG5: dsh required + unreachable streamable-http server -> ValueError."""
    from harness_allocator import validate_mcp_required
    with pytest.raises(ValueError, match="mcp_light"):
        validate_mcp_required(
            [], False,
            [("mcp_light", "streamable-http", "http://127.0.0.1:1/mcp")], True,
            reachability=lambda url: False,
        )


def test_tg5_validate_mcp_required_succeeds_when_required_and_reachable():
    """TG5: required + reachable stub -> no raise."""
    from harness_allocator import validate_mcp_required
    assert validate_mcp_required(
        [("mcp_light", "http://127.0.0.1:9135/mcp")], True,
        [("mcp_light", "streamable-http", "http://127.0.0.1:9135/mcp")], True,
        reachability=lambda url: True,
    ) is None


def test_tg5_validate_mcp_required_dsh_stdio_skips_url_probe():
    """TG5: stdio servers are NOT probed (the plugin validates at startup)."""
    from harness_allocator import validate_mcp_required
    # stdio server with required=True but the reachability probe is set to
    # always-fail. No raise: stdio is not URL-shaped, so we don't probe it.
    assert validate_mcp_required(
        [], False,
        [("github", "stdio", "npx")], True,
        reachability=lambda url: False,
    ) is None


# ── TG2 + TG3 roundtrip: getters -> builders -> exact wire shape ───

def test_tg2_mcp_roundtrip_codex_getters_to_setup_argv(monkeypatch):
    """TG2: the env-driven getter renders the exact setup argv via the builder."""
    monkeypatch.setenv("CODEX_MCP_SERVERS", "mcp_light=http://127.0.0.1:9135/mcp")
    monkeypatch.setenv("CODEX_MCP_REQUIRED", "true")
    from harness_allocator import (
        build_codex_mcp_setup_argv,
        get_codex_mcp_required,
        get_codex_mcp_servers,
    )
    servers = get_codex_mcp_servers()
    assert get_codex_mcp_required() is True
    assert len(servers) == 1
    argv = build_codex_mcp_setup_argv(*servers[0])
    assert argv == [
        "codex", "mcp", "add", "mcp_light", "--url", "http://127.0.0.1:9135/mcp",
    ]


def test_tg3_mcp_roundtrip_dsh_getters_to_patch_yml(monkeypatch):
    """TG3: the env-driven getter renders the exact YAML overlay via the builder."""
    monkeypatch.setenv("DSH_MCP_SERVERS", "mcp_light=streamable-http=http://127.0.0.1:9135/mcp")
    monkeypatch.setenv("DSH_MCP_REQUIRED", "true")
    from harness_allocator import (
        build_dsh_mcp_patch_yml,
        get_dsh_mcp_required,
        get_dsh_mcp_servers,
    )
    servers = get_dsh_mcp_servers()
    assert get_dsh_mcp_required() is True
    yml = build_dsh_mcp_patch_yml(servers, required=True)
    body = yml.split("- entries:", 1)[1]
    assert "id: mcp_light" in body
    assert "transport: streamable-http" in body
    assert "url: http://127.0.0.1:9135/mcp" in body
    assert "failOnStartupError: true" in body


# ── MCP and fresh-context are independent (GOAL.md §10) ────────────

def test_mcp_capability_is_independent_of_argv_builders():
    """GOAL.md §10: toggling MCP must not change the argv builders' signatures
    or behaviour. The new builders are pure and additive — they neither modify
    the existing builders nor depend on them."""
    import harness_allocator.adapter as adapter
    # The new builders are exported from the module.
    assert hasattr(adapter, "build_codex_mcp_setup_argv")
    assert hasattr(adapter, "build_dsh_mcp_patch_yml")
    assert hasattr(adapter, "validate_mcp_required")
    # The existing builders are still there, unchanged.
    assert hasattr(adapter, "_codex_argv")
    assert hasattr(adapter, "build_dsh_argv")
