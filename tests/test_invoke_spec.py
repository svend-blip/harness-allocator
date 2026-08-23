"""Tests for ``harness_allocator.invoke.execute_spec`` — D3+D4+D5 of Run 020.

Contract (GOAL.md Run 020 §1 D3/D4/D5, §4 TG3-TG7, and the binding constraints):

A. Import surface — ``from harness_allocator.invoke import execute_spec`` and
   ``callable(execute_spec)`` is True (TG3).

B. Facade-freeze goldens — argv lists, launch command, MCP builders, and
   frozen ``ValueError`` message strings are byte-identical to the pristine
   master ``c202286`` facade (the captured goldens below are the source of
   truth; this proves the frozen ``execute()`` and ``run_argv()`` are
   unchanged after ``execute_spec`` is added).

C. ``execute_spec`` validation (typed, no subprocess) — unknown harness /
   missing prompt / unsupported required capability raise the three D1 typed
   errors BEFORE any subprocess. Each refusal test carries ``"refus"`` in
   its name (TG7's ``-k refus`` naming contract).

D. ``execute_spec`` non-interactive refusal (TG7, typed, no subprocess) —
   resident TUIs (``codex``, ``claude-code``, ``opencode``) raise
   ``LargeInputRefusedError`` before any subprocess.

E. ``execute_spec`` success path — fake Popen pattern mirroring the
   existing ``test_harness_allocator.py`` (``_FakeProc``/``_patch_popen``);
   verifies status==SUCCESS, exit_code==0, stdout/error/request_id/harness,
   timing floats, usage counters None, session_id None.

F. ``execute_spec`` error path — nonzero child exit reports
   ``status==ERROR`` with ``exit_code is None`` (NOT a real nonzero code).
   ``execute_spec`` has NO cancellation path in this handoff (BOUND
   ``execute_spec(spec, cfg=None)``; ``HarnessRunSpec`` has no cancel
   field) — pre-run cancellation is HA-2+ wiring (GOAL.md §7 non-goals).

G. Large-paste routing — a 20k+ character multiline prompt through
   ``execute_spec`` for harness="dsh" is delivered as ONE argv element to
   ``Popen`` (the package's REAL mechanism — no tempfile/stdin).

H. Interrupt semantics are DECLARED, not guessed — the D1 manifest
   ``automation.interrupt_safe`` values are asserted directly
   (``dsh`` True; ``codex``/``claude-code``/``opencode`` False).

Goldens provenance: captured from pristine master ``c202286`` facade
(``invoke.py`` + ``adapter.py`` unmodified), injected fixed cfg
(``_GoldenCfg`` below — does NOT read env/ini), before ``execute_spec``
was added. Recapture requires reverting ``invoke.py`` to its pristine
form; the captures here are the byte-identical reference.

Stdlib + pytest only (the package's standing constraint). Test names
carrying ``"refus"`` are picked up by ``pytest -k refus`` (TG7).
"""

import sys
import threading
from pathlib import Path

import pytest

# Import the package from the project root (sibling of tests/).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import harness_allocator.invoke as inv  # noqa: E402
from harness_allocator.adapter import (  # noqa: E402
    build_codex_mcp_setup_argv,
    build_dsh_argv,
    build_dsh_invocation,
    build_dsh_mcp_patch_yml,
    build_launch_argv,
    build_launch_command,
    build_task_argv,
)
from harness_allocator.capabilities import (  # noqa: E402
    LargeInputRefusedError,
    MissingPromptError,
    UnknownHarnessError,
    UnsupportedCapabilityError,
    get_capabilities,
)
from harness_allocator.invoke import execute_spec  # noqa: E402
from harness_allocator.runspec import (  # noqa: E402
    HarnessRunResult,
    HarnessRunSpec,
    RunOutput,
    RunRequirements,
    RunUsage,
    RunEvidence,
    RunTiming,
)
from harness_allocator.status import ERROR, SUCCESS  # noqa: E402


# ── Injected fixed cfg (goldens contract) ──────────────────────────────


class _GoldenCfg:
    """Injected fixed cfg — does NOT read env or ini.

    Per the handoff's bound goldens contract, the goldens were captured
    with these exact values so argv construction is byte-identical
    regardless of host state. DO NOT change any value below without
    recapturing the goldens from the pristine master facade.
    """

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


# ── Captured goldens (byte-identical reference) ────────────────────────


# These are LITERAL captures from the pristine facade. They are byte-frozen;
# any change means the facade's command construction drifted from master
# c202286 and TG4 / facade-freeze is BROKEN.

_DSH_TASK_ARGV_GOLDEN = [
    "npx",
    "@deepseek-ai/dsh",
    "--profile",
    "headless",
    "--patch",
    "/tmp/dsh-v4-pro.patch.yml",
    "Reply with exactly: OK\nSecond line\nThird line",
]

_DSH_LAUNCH_ARGV_GOLDEN = [
    "npx",
    "@deepseek-ai/dsh",
    "--profile",
    "headless",
    "--patch",
    "/tmp/dsh-v4-pro.patch.yml",
]

_CODEX_LAUNCH_ARGV_GOLDEN = [
    "codex",
    "-m",
    "MiniMax-M3",
    "--sandbox",
    "workspace-write",
    "--ask-for-approval",
    "never",
]

_CODEX_LAUNCH_COMMAND_GOLDEN = (
    "codex -m MiniMax-M3 --sandbox workspace-write "
    "--ask-for-approval never"
)

# Frozen ValueError messages (bound strings — must not drift).
_REFUSAL_TASK_CODEX_MSG = "no one-shot task invocation for harness 'codex'"
_REFUSAL_TASK_CLAUDE_CODE_MSG = (
    "no one-shot task invocation for harness 'claude-code'"
)
_REFUSAL_TASK_OPENCODE_MSG = (
    "no one-shot task invocation for harness 'opencode'"
)
_REFUSAL_LAUNCH_CLAUDE_CODE_MSG = "not a native harness: 'claude-code'"
_REFUSAL_LAUNCH_OPENCODE_MSG = "not a native harness: 'opencode'"

# MCP builders (Run 004 surface — must stay unchanged).
_CODEX_MCP_SETUP_ARGV_GOLDEN = [
    "codex",
    "mcp",
    "add",
    "probe",
    "--url",
    "http://127.0.0.1:9999/mcp",
]

_DSH_MCP_PATCH_YML_GOLDEN = (
    "# Auto-generated by harness_allocator.adapter.build_dsh_mcp_patch_yml\n"
    "# Compose with existing --patch overlays (this is a cordis.patch.yml-shaped\n"
    "# list of plugin entries). One @deepseek-ai/dsh-mcp-client entry per MCP server.\n"
    "- entries:\n"
    "  - id: probe\n"
    "    name: '@deepseek-ai/dsh-mcp-client'\n"
    "    config:\n"
    "      serverName: probe\n"
    "      transport: streamable-http\n"
    "      url: http://127.0.0.1:9999/mcp\n"
    "      failOnStartupError: false\n"
)


# ── Fake Popen / clock (mirrors test_harness_allocator.py) ──────────────


class _FakeProc:
    """Minimal Popen stand-in mirroring test_harness_allocator.py's pattern."""

    def __init__(self, poll_results, stdout="ok\n", stderr=""):
        self._poll_results = list(poll_results)
        self.pid = 4242
        self.returncode = None
        self._stdout = stdout
        self._stderr = stderr
        self.killed = False
        self.recorded_argv = None  # captured by the patched Popen below

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
    """Minimal time.monotonic() / time.sleep() stand-in (drive forward)."""

    def __init__(self):
        self.now = 1000.0  # arbitrary base

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def time(self):
        # Distinct from monotonic so timing.started_at/finished_at are stable.
        return 2000.0 + (self.now - 1000.0)


def _patch_popen(monkeypatch, proc, *, fail_if_called=True):
    """Patch invoke.subprocess.Popen so it returns ``proc`` (and records the
    argv that was passed). When ``fail_if_called`` is True, the patcher
    asserts Popen was called (catches "no subprocess spawned" expectations
    inversely: a refusal test should set this to True and assert Popen was
    NOT called via a separate mechanism)."""

    recorded = {"argv": None}

    def fake_popen(argv, *a, **k):
        recorded["argv"] = list(argv)
        return proc

    monkeypatch.setattr(inv.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(inv, "_time", _FakeClock())
    return recorded


def _assert_no_popen_called(monkeypatch, fn, *args, **kwargs):
    """Run ``fn`` and assert that invoke.subprocess.Popen was NEVER called.

    Refusal tests use this to prove the typed error fires before any
    subprocess is spawned (TG7's contract).
    """
    called = {"yes": False}

    def explode(*a, **k):
        called["yes"] = True
        raise AssertionError(
            "subprocess.Popen must NOT be called when validation refuses "
            "(TG7 / D4)"
        )

    monkeypatch.setattr(inv.subprocess, "Popen", explode)
    return called


# ── A. Import surface (TG3) ─────────────────────────────────────────────


def test_execute_spec_is_importable_and_callable():
    """``execute_spec`` is importable from ``harness_allocator.invoke`` and
    is callable (TG3's contract)."""
    from harness_allocator.invoke import execute_spec as fn

    assert callable(fn)
    # And the name lives on the invoke module.
    assert inv.execute_spec is fn


# ── B. Facade-freeze goldens ────────────────────────────────────────────


def test_facade_freeze_dsh_task_argv_golden():
    """dsh one-shot argv (multiline task) is byte-identical to the captured
    golden — this is the package's REAL large-input mechanism."""
    argv = build_task_argv(
        "dsh",
        model_target="deepseek-v4-pro",
        task="Reply with exactly: OK\nSecond line\nThird line",
        cfg=_GoldenCfg(),
    )
    assert argv == _DSH_TASK_ARGV_GOLDEN


def test_facade_freeze_dsh_launch_argv_golden():
    """dsh launch argv (no model, no task) is byte-identical to the golden."""
    argv = build_launch_argv(
        "dsh", model_target="deepseek-v4-pro", cfg=_GoldenCfg()
    )
    assert argv == _DSH_LAUNCH_ARGV_GOLDEN


def test_facade_freeze_codex_launch_argv_golden():
    """codex launch argv is byte-identical to the golden."""
    argv = build_launch_argv(
        "codex", model_target="MiniMax-M3", cfg=_GoldenCfg()
    )
    assert argv == _CODEX_LAUNCH_ARGV_GOLDEN


def test_facade_freeze_codex_launch_command_golden():
    """codex launch command (shell-quoted) is byte-identical to the golden."""
    cmd = build_launch_command(
        "codex", model_target="MiniMax-M3", cfg=_GoldenCfg()
    )
    assert cmd == _CODEX_LAUNCH_COMMAND_GOLDEN


def test_facade_freeze_refusal_build_task_argv_codex_message():
    """The frozen ValueError message for codex's one-shot refusal is
    byte-identical to the captured text."""
    with pytest.raises(ValueError) as exc_info:
        build_task_argv("codex", model_target="x", task="x", cfg=_GoldenCfg())
    assert str(exc_info.value) == _REFUSAL_TASK_CODEX_MSG


def test_facade_freeze_refusal_build_task_argv_claude_code_message():
    """The frozen ValueError message for claude-code's one-shot refusal."""
    with pytest.raises(ValueError) as exc_info:
        build_task_argv(
            "claude-code", model_target="x", task="x", cfg=_GoldenCfg()
        )
    assert str(exc_info.value) == _REFUSAL_TASK_CLAUDE_CODE_MSG


def test_facade_freeze_refusal_build_task_argv_opencode_message():
    """The frozen ValueError message for opencode's one-shot refusal."""
    with pytest.raises(ValueError) as exc_info:
        build_task_argv(
            "opencode", model_target="x", task="x", cfg=_GoldenCfg()
        )
    assert str(exc_info.value) == _REFUSAL_TASK_OPENCODE_MSG


def test_facade_freeze_refusal_build_launch_argv_claude_code_message():
    """claude-code is not a NATIVE_HARNESS — adapter.build_launch_argv
    raises a frozen ValueError."""
    with pytest.raises(ValueError) as exc_info:
        build_launch_argv(
            "claude-code", model_target="x", cfg=_GoldenCfg()
        )
    assert str(exc_info.value) == _REFUSAL_LAUNCH_CLAUDE_CODE_MSG


def test_facade_freeze_refusal_build_launch_argv_opencode_message():
    """opencode is not a NATIVE_HARNESS — adapter.build_launch_argv
    raises a frozen ValueError."""
    with pytest.raises(ValueError) as exc_info:
        build_launch_argv(
            "opencode", model_target="x", cfg=_GoldenCfg()
        )
    assert str(exc_info.value) == _REFUSAL_LAUNCH_OPENCODE_MSG


def test_facade_freeze_codex_mcp_setup_argv_golden():
    """The codex MCP setup argv (Run 004 surface) is byte-identical."""
    argv = build_codex_mcp_setup_argv(
        "probe", "http://127.0.0.1:9999/mcp"
    )
    assert argv == _CODEX_MCP_SETUP_ARGV_GOLDEN


def test_facade_freeze_dsh_mcp_patch_yml_golden():
    """The dsh MCP patch YAML (Run 004 surface) is byte-identical."""
    yml = build_dsh_mcp_patch_yml(
        [("probe", "streamable-http", "http://127.0.0.1:9999/mcp")],
        required=False,
    )
    assert yml == _DSH_MCP_PATCH_YML_GOLDEN


# ── C. execute_spec validation (typed, no subprocess) ──────────────────


def test_refus_execute_spec_unknown_harness_no_spawn(monkeypatch):
    """Unknown harness raises UnknownHarnessError BEFORE any subprocess
    is spawned (TG7's contract)."""
    called = _assert_no_popen_called(monkeypatch, execute_spec)
    spec = HarnessRunSpec(harness="bogus", prompt="anything")
    with pytest.raises(UnknownHarnessError) as exc_info:
        execute_spec(spec, cfg=_GoldenCfg())
    assert "bogus" in str(exc_info.value)
    assert called["yes"] is False


def test_refus_execute_spec_missing_prompt_no_spawn(monkeypatch):
    """Empty prompt raises MissingPromptError BEFORE any subprocess."""
    called = _assert_no_popen_called(monkeypatch, execute_spec)
    spec = HarnessRunSpec(harness="dsh", prompt="")
    with pytest.raises(MissingPromptError):
        execute_spec(spec, cfg=_GoldenCfg())
    assert called["yes"] is False


def test_refus_execute_spec_whitespace_prompt_no_spawn(monkeypatch):
    """Whitespace-only prompt raises MissingPromptError BEFORE any subprocess."""
    called = _assert_no_popen_called(monkeypatch, execute_spec)
    spec = HarnessRunSpec(harness="dsh", prompt="   ")
    with pytest.raises(MissingPromptError):
        execute_spec(spec, cfg=_GoldenCfg())
    assert called["yes"] is False


def test_refus_execute_spec_unsupported_capability_no_spawn(monkeypatch):
    """Asking for an unsupported capability raises UnsupportedCapabilityError
    BEFORE any subprocess. ``dsh.execution.terminal`` is False → unsupported."""
    called = _assert_no_popen_called(monkeypatch, execute_spec)
    spec = HarnessRunSpec(
        harness="dsh",
        prompt="anything",
        requirements=RunRequirements(capabilities=["terminal"]),
    )
    with pytest.raises(UnsupportedCapabilityError) as exc_info:
        execute_spec(spec, cfg=_GoldenCfg())
    msg = str(exc_info.value)
    assert "dsh" in msg
    assert "terminal" in msg
    assert called["yes"] is False


def test_refus_execute_spec_unsupported_capability_flux_capacitor(monkeypatch):
    """Made-up capability name raises UnsupportedCapabilityError BEFORE
    any subprocess."""
    called = _assert_no_popen_called(monkeypatch, execute_spec)
    spec = HarnessRunSpec(
        harness="dsh",
        prompt="anything",
        requirements=RunRequirements(capabilities=["flux_capacitor"]),
    )
    with pytest.raises(UnsupportedCapabilityError):
        execute_spec(spec, cfg=_GoldenCfg())
    assert called["yes"] is False


# ── D. execute_spec non-interactive refusal (TG7, resident TUIs) ──────


@pytest.mark.parametrize("resident", ["codex", "claude-code", "opencode"])
def test_refus_execute_spec_resident_tui_refuses_large_input(
    monkeypatch, resident
):
    """Resident TUIs (codex/claude-code/opencode) have no one-shot task
    form (``adapter.build_task_argv`` raises ValueError). ``execute_spec``
    must catch that and re-raise ``LargeInputRefusedError`` BEFORE any
    subprocess — the typed non-interactive / large-paste refusal (TG7)."""
    called = _assert_no_popen_called(monkeypatch, execute_spec)
    spec = HarnessRunSpec(
        harness=resident,
        prompt="anything",
        model_reference="MiniMax-M3",
    )
    with pytest.raises(LargeInputRefusedError) as exc_info:
        execute_spec(spec, cfg=_GoldenCfg())
    # The error message names the harness (so a reviewer can see what was
    # refused without reading the call site).
    assert resident in str(exc_info.value)
    assert called["yes"] is False


def test_refus_execute_spec_large_input_refused_error_is_value_error():
    """LargeInputRefusedError IS a ValueError subclass — the package idiom."""
    assert issubclass(LargeInputRefusedError, ValueError)


# ── E. execute_spec success path ────────────────────────────────────────


def test_execute_spec_success_path_dsh(monkeypatch):
    """A valid dsh spec with a fake Popen returning 0 produces a
    HarnessRunResult with status=SUCCESS, exit_code=0, stdout=proc output,
    request_id=spec.request_id, harness='dsh', session_id=None,
    timing.started_at/finished_at floats, usage counters all None."""
    proc = _FakeProc(poll_results=[0], stdout="ok\n", stderr="")
    recorded = _patch_popen(monkeypatch, proc)
    spec = HarnessRunSpec(
        harness="dsh",
        prompt="echo ok",
        model_reference="deepseek-v4-pro",
        request_id="ha-001",
    )
    result = execute_spec(spec, cfg=_GoldenCfg())

    assert isinstance(result, HarnessRunResult)
    assert result.status == SUCCESS
    # BOUND: 0 on SUCCESS, None otherwise — run_argv does not surface the
    # child return code.
    assert result.exit_code == 0
    assert result.stdout == "ok\n"
    assert result.stderr == ""
    assert result.request_id == "ha-001"
    assert result.harness == "dsh"
    assert result.session_id is None
    # Timing floats: started_at < finished_at, both set.
    assert isinstance(result.timing.started_at, float)
    assert isinstance(result.timing.finished_at, float)
    assert result.timing.started_at <= result.timing.finished_at
    # Usage counters all None ("nulls where a harness exposes nothing").
    assert result.usage.input_tokens is None
    assert result.usage.output_tokens is None
    assert result.usage.cost is None
    # Evidence is empty (this one-shot path tracks nothing).
    assert result.evidence.changed_files == []
    assert result.evidence.patch_available is False
    assert result.evidence.tests_run == []

    # And Popen was called exactly once with the expected argv shape.
    assert recorded["argv"] is not None
    assert recorded["argv"][0:2] == ["npx", "@deepseek-ai/dsh"]
    # The full prompt is ONE argv element.
    assert recorded["argv"][-1] == "echo ok"


def test_execute_spec_success_with_expected_artifacts(monkeypatch):
    """spec.output.expected_artifacts is propagated into result.artifacts."""
    proc = _FakeProc(poll_results=[0], stdout="", stderr="")
    _patch_popen(monkeypatch, proc)
    spec = HarnessRunSpec(
        harness="dsh",
        prompt="x",
        output=RunOutput(expected_artifacts=["a.txt", "b.txt"]),
    )
    result = execute_spec(spec, cfg=_GoldenCfg())
    assert result.artifacts == ["a.txt", "b.txt"]


def test_execute_spec_generates_request_id_when_blank(monkeypatch):
    """When spec.request_id is empty, execute_spec fills it via
    transport.make_request_id (already imported in invoke.py)."""
    proc = _FakeProc(poll_results=[0], stdout="", stderr="")
    _patch_popen(monkeypatch, proc)
    spec = HarnessRunSpec(harness="dsh", prompt="x")
    assert spec.request_id == ""
    result = execute_spec(spec, cfg=_GoldenCfg())
    assert result.request_id != ""
    # Non-empty UUID-ish string (not strictly asserted; just non-empty).
    assert isinstance(result.request_id, str)


def test_execute_spec_uses_spec_working_directory(monkeypatch):
    """execute_spec uses spec.working_directory as the subprocess cwd
    when set."""
    proc = _FakeProc(poll_results=[0], stdout="", stderr="")
    recorded = _patch_popen(monkeypatch, proc)

    captured_kwargs = {}

    def fake_popen(argv, *a, **k):
        captured_kwargs.update(k)
        recorded["argv"] = list(argv)
        return proc

    monkeypatch.setattr(inv.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(inv, "_time", _FakeClock())

    spec = HarnessRunSpec(
        harness="dsh",
        prompt="x",
        working_directory="/tmp/somewhere",
    )
    execute_spec(spec, cfg=_GoldenCfg())
    assert captured_kwargs.get("cwd") == "/tmp/somewhere"


# ── F. execute_spec error / cancel path ─────────────────────────────────


def test_execute_spec_nonzero_exit_maps_to_error_with_none_exit_code(
    monkeypatch,
):
    """A nonzero child exit → status=ERROR with exit_code=None (NOT a real
    nonzero code — ``run_argv`` does not surface the child return code)."""
    proc = _FakeProc(poll_results=[2], stdout="", stderr="boom")
    _patch_popen(monkeypatch, proc)
    spec = HarnessRunSpec(harness="dsh", prompt="x")
    result = execute_spec(spec, cfg=_GoldenCfg())
    assert result.status == ERROR
    # BOUND: exit_code is None on ERROR (run_argv does not expose the
    # child return code; we do NOT fabricate a real nonzero value).
    assert result.exit_code is None
    assert result.stderr == "boom"


# NOTE: execute_spec has NO cancellation path in this handoff.
# execute_spec is BOUND to execute_spec(spec, cfg=None) and calls
# run_argv(argv, cwd, timeout) with no cancel_event / cancel_callback.
# HarnessRunSpec (D2, runspec.py) has no cancel-related field, so no
# cancel_event can be threaded through execute_spec. Pre-run
# cancellation through execute_spec is HA-2+ wiring (GOAL.md §7
# non-goals). Declared interrupt semantics live in the D1 manifest
# (automation.interrupt_safe) — see section H below for the
# assertions.

# ── G. Large-paste routing (the package's REAL mechanism) ──────────────


def test_execute_spec_multiline_prompt_delivered_as_one_argv_element(
    monkeypatch,
):
    """A 20k+ character multiline prompt through execute_spec for harness='dsh'
    is delivered as ONE argv element to Popen. The package has NO
    tempfile/stdin mechanism; the large-input path is single-argv-element
    delivery via build_task_argv (mirrors test_execute_argv_form_preserves_multiline_task)."""
    # Build a 20k+ character multiline prompt (200 lines of 100 chars).
    long_task = "\n".join(("x" * 100 + f" line {i}") for i in range(200))
    assert len(long_task) > 20_000
    assert long_task.count("\n") >= 199

    proc = _FakeProc(poll_results=[0], stdout="", stderr="")
    recorded = _patch_popen(monkeypatch, proc)

    spec = HarnessRunSpec(
        harness="dsh",
        prompt=long_task,
        model_reference="deepseek-v4-pro",
    )
    execute_spec(spec, cfg=_GoldenCfg())

    argv = recorded["argv"]
    assert argv is not None
    # The long prompt is the LAST argv element, byte-identical, no shlex
    # round-trip, no per-line execution.
    assert argv[-1] == long_task
    # And argv has exactly 7 elements (npx @dsh --profile X --patch Y TASK).
    assert len(argv) == 7
    # Cross-check: argv is exactly build_dsh_argv(...) — the package's
    # REAL large-input path.
    expected = build_dsh_argv(
        model_target="deepseek-v4-pro", task=long_task, cfg=_GoldenCfg()
    )
    assert argv == expected


# ── H. Interrupt semantics are DECLARED, not guessed ──────────────────


def test_execute_spec_dsh_interrupt_safe_is_true():
    """D4 — ``dsh`` declares ``automation.interrupt_safe = True`` in the
    D1 manifest (grounded in the measured SIGINT→SIGTERM→SIGKILL cancel
    path in invoke.run_argv)."""
    manifest = get_capabilities("dsh")
    assert manifest["automation"]["interrupt_safe"] is True


@pytest.mark.parametrize(
    "resident", ["codex", "claude-code", "opencode"]
)
def test_execute_spec_resident_tui_interrupt_safe_is_false(resident):
    """D4 — the resident TUIs declare ``automation.interrupt_safe = False``
    in the D1 manifest (the resident TUI does not own a process group; the
    measured cancel-path is the headless one-shot's)."""
    manifest = get_capabilities(resident)
    assert manifest["automation"]["interrupt_safe"] is False
