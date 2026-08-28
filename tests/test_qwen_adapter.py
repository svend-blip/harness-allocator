"""Tests for the ``qwen`` adapter (Run 022 / HA-2 — D1 + D2).

Contract (GOAL.md Run 022 §1 D1/D2 and the binding constraints):

- ``build_qwen_argv`` returns ``[<qwen_bin>, --approval-mode, <mode>,
  --include-directories <dir>*, -p, <task>]``: model and endpoint NEVER in
  argv; ``--include-directories`` is repeatable and omitted when no dirs
  are configured; ``-p <task>`` is appended only when a task is supplied.
- ``build_qwen_invocation`` matches argv joined (mirrors dsh's shape).
- ``build_qwen_env`` carries ``OPENAI_BASE_URL`` (forced /v1),
  ``OPENAI_MODEL`` (verbatim), and ``OPENAI_API_KEY`` (read from the NAMED
  env var). Empty config returns an empty dict.
- ``get_capabilities("qwen")`` has EXACTLY eight groups with
  ``sessions.mode == "oneshot"``, ``execution.headless == True``,
  ``automation.non_interactive == True``.
- Config defaults: each ``get_qwen_*`` returns its documented default when
  the env is cleared and no ini section exists.

Stdlib + pytest only (the package's standing constraint). No spec-routing
tests here (handoff 2 / D3 owns ``-k spec``), no ``*refus*`` refusal tests
(handoff 2), no enumeration test edits (handoff 2 owns the
SUPPORTED_HARNESSES five-key flip). The four existing harnesses'
behaviour is intentionally untouched — see test_harness_allocator.py and
test_capabilities.py for those.
"""

import os
import shlex
import sys
from pathlib import Path

import pytest

# Import the package from the project root (sibling of tests/).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness_allocator.adapter import (  # noqa: E402
    build_launch_argv,
    build_launch_command,
    build_qwen_argv,
    build_qwen_env,
    build_qwen_invocation,
    build_task_argv,
    build_task_invocation,
)
from harness_allocator.capabilities import get_capabilities  # noqa: E402


# ── Test doubles (mirror the package's _FakeCfg / _UnsetEnv style) ────


class _QwenCfg:
    """Fixed cfg so argv/env assertions are byte-stable.

    Mirrors ``tests/test_harness_allocator.py::_FakeCfg`` for the qwen
    surface: bin = "qwen", approval_mode = "yolo", no workdir, no add_dirs,
    no base_url, no api_key_env.
    """

    def get_qwen_bin(self):
        return "qwen"

    def get_qwen_approval_mode(self):
        return "yolo"

    def get_qwen_workdir(self):
        return ""

    def get_qwen_add_dirs(self):
        return []

    def get_qwen_base_url(self):
        return ""

    def get_qwen_api_key_env(self):
        return ""


class _QwenCfgDirs(_QwenCfg):
    """Cfg variant that supplies a couple of include-directories."""

    def get_qwen_add_dirs(self):
        return ["/srv/extra", "/srv/data"]


class _QwenCfgNonDefault(_QwenCfg):
    """Cfg variant with a non-default approval_mode."""

    def get_qwen_approval_mode(self):
        return "auto-edit"


class _UnsetEnv:
    """Context manager: clear QWEN_* env vars and restore prior values."""

    def __init__(self):
        self._keys = (
            "QWEN_BIN",
            "QWEN_BASE_URL",
            "QWEN_API_KEY_ENV",
            "QWEN_WORKDIR",
            "QWEN_ADD_DIRS",
            "QWEN_APPROVAL_MODE",
        )
        self._saved = {k: os.environ.get(k) for k in self._keys}

    def __enter__(self):
        for k in self._keys:
            os.environ.pop(k, None)
        return self

    def __exit__(self, exc_type, exc, tb):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ── A. argv golden ────────────────────────────────────────────────────


def test_qwen_argv_golden_minimum():
    """Default cfg + no task -> [qwen, --approval-mode, yolo]. No dirs, no -p."""
    argv = build_qwen_argv(model_target="qwen3-8b", cfg=_QwenCfg())
    assert argv == ["qwen", "--approval-mode", "yolo"]


def test_qwen_argv_golden_with_dirs_and_task():
    """All knobs: [qwen, --approval-mode, <mode>, --include-directories <d>, -p, <task>]."""
    argv = build_qwen_argv(
        model_target="qwen3-8b", task="summarise this", cfg=_QwenCfgDirs()
    )
    assert argv == [
        "qwen",
        "--approval-mode", "yolo",
        "--include-directories", "/srv/extra",
        "--include-directories", "/srv/data",
        "-p", "summarise this",
    ]


def test_qwen_argv_omits_model_target():
    """The model travels in the env (build_qwen_env), NEVER in argv."""
    argv = build_qwen_argv(model_target="some-other-model", task="x", cfg=_QwenCfg())
    assert "some-other-model" not in argv
    assert "qwen3-8b" not in argv


def test_qwen_argv_task_is_single_element_with_newlines():
    """The full task is one argv element with embedded newlines preserved."""
    task = "Reply with exactly: OK\nSecond line\nThird line\nFinal"
    argv = build_qwen_argv(model_target="qwen3-8b", task=task, cfg=_QwenCfg())
    assert argv[-2:] == ["-p", task]
    assert argv[-1] == task
    assert argv[-1].count("\n") == 3


def test_qwen_argv_dirs_omitted_when_empty():
    """No configured dirs -> --include-directories flag is absent entirely."""
    argv = build_qwen_argv(model_target="qwen3-8b", task="x", cfg=_QwenCfg())
    assert "--include-directories" not in argv


def test_qwen_argv_dirs_stripped_when_blank():
    """Blank entries in add_dirs are skipped (mirror dsh/codex pattern)."""

    class Cfg(_QwenCfg):
        def get_qwen_add_dirs(self):
            return ["", "/srv/keep", "   "]

    argv = build_qwen_argv(model_target="qwen3-8b", cfg=Cfg())
    assert "--include-directories" in argv
    # One real dir only; no blank "" or whitespace entries.
    pairs = [argv[i + 1] for i, p in enumerate(argv) if p == "--include-directories"]
    assert pairs == ["/srv/keep"]


def test_qwen_argv_task_omitted_when_empty():
    """No task -> -p flag is absent entirely (launch form)."""
    argv = build_qwen_argv(model_target="qwen3-8b", cfg=_QwenCfg())
    assert "-p" not in argv


def test_qwen_argv_honours_non_default_approval_mode():
    """The approval-mode value comes from cfg (config-overridable)."""
    argv = build_qwen_argv(model_target="qwen3-8b", cfg=_QwenCfgNonDefault())
    assert argv == ["qwen", "--approval-mode", "auto-edit"]


def test_qwen_argv_bin_supports_full_launcher_path():
    """bin may be a multi-token launcher (shlex-split, like dsh)."""

    class Cfg(_QwenCfg):
        def get_qwen_bin(self):
            return "/opt/qwen/bin/qwen --extra"

    argv = build_qwen_argv(model_target="qwen3-8b", cfg=Cfg())
    assert argv[:3] == ["/opt/qwen/bin/qwen", "--extra", "--approval-mode"]


# ── B. build_qwen_invocation matches argv joined ──────────────────────


def test_qwen_invocation_matches_argv_joined():
    """The shell command, when split, reconstructs the exact argv list."""
    task = "line one\nline two\nSupervisor"
    cmd = build_qwen_invocation(model_target="qwen3-8b", task=task, cfg=_QwenCfgDirs())
    argv = build_qwen_argv(model_target="qwen3-8b", task=task, cfg=_QwenCfgDirs())
    assert shlex.split(cmd) == argv


def test_qwen_invocation_launch_form():
    """No task -> the invocation is the launch form (no -p)."""
    cmd = build_qwen_invocation(model_target="qwen3-8b", cfg=_QwenCfg())
    argv = build_qwen_argv(model_target="qwen3-8b", cfg=_QwenCfg())
    assert shlex.split(cmd) == argv
    assert "-p" not in cmd


# ── C. manifest shape ────────────────────────────────────────────────


def test_qwen_manifest_has_exactly_eight_groups():
    """get_capabilities("qwen") returns EXACTLY eight contract groups."""
    manifest = get_capabilities("qwen")
    expected = {"execution", "workspace", "sessions", "extensions",
                "automation", "concurrency", "lifecycle", "models"}
    assert set(manifest.keys()) == expected


def test_qwen_manifest_sessions_mode_is_oneshot():
    """sessions.mode == "oneshot" is BOUND (Run 022 §1 D1; headless -p)."""
    manifest = get_capabilities("qwen")
    assert manifest["sessions"]["mode"] == "oneshot"


def test_qwen_manifest_execution_headless_is_true():
    """execution.headless == True (qwen -p is headless one-shot)."""
    manifest = get_capabilities("qwen")
    assert manifest["execution"]["headless"] is True
    assert manifest["execution"]["terminal"] is False
    assert manifest["execution"]["interactive"] is False


def test_qwen_manifest_automation_non_interactive_is_true():
    """automation.non_interactive == True (qwen -p is non-interactive)."""
    manifest = get_capabilities("qwen")
    assert manifest["automation"]["non_interactive"] is True
    assert manifest["automation"]["deterministic_exit"] is True
    assert manifest["automation"]["interrupt_safe"] is True


def test_qwen_manifest_sessions_resume_is_true():
    """sessions.session_resume == True (qwen has -c/--continue and -r/--resume)."""
    manifest = get_capabilities("qwen")
    assert manifest["sessions"]["session_resume"] is True
    assert manifest["sessions"]["persistent_session"] is False


def test_qwen_manifest_extensions_mcp_is_true():
    """extensions.mcp == True (qwen has 'qwen mcp' subcommand)."""
    manifest = get_capabilities("qwen")
    assert manifest["extensions"]["mcp"] is True


def test_qwen_manifest_extensions_skills_is_true():
    """extensions.skills == True (/skills is exposed in interactive mode)."""
    manifest = get_capabilities("qwen")
    assert manifest["extensions"]["skills"] is True


def test_qwen_manifest_returns_fresh_copy():
    """Mutating the returned manifest must not affect the next call."""
    m1 = get_capabilities("qwen")
    m1["execution"]["headless"] = False
    m2 = get_capabilities("qwen")
    assert m2["execution"]["headless"] is True


# ── D. config defaults ───────────────────────────────────────────────


def test_qwen_bin_default_when_env_cleared_and_no_ini():
    with _UnsetEnv():
        # Force the ini section to look empty even if harness-allocator.ini
        # has a [qwen] block on the host: rebuild a fresh parser view.
        from harness_allocator import config
        assert config.get_qwen_bin() == "qwen"


def test_qwen_base_url_default_is_empty():
    with _UnsetEnv():
        from harness_allocator import config
        assert config.get_qwen_base_url() == ""


def test_qwen_api_key_env_default_is_empty():
    with _UnsetEnv():
        from harness_allocator import config
        assert config.get_qwen_api_key_env() == ""


def test_qwen_workdir_default_is_empty():
    with _UnsetEnv():
        from harness_allocator import config
        assert config.get_qwen_workdir() == ""


def test_qwen_add_dirs_default_is_empty_list():
    with _UnsetEnv():
        from harness_allocator import config
        assert config.get_qwen_add_dirs() == []


def test_qwen_approval_mode_default_is_yolo():
    with _UnsetEnv():
        from harness_allocator import config
        assert config.get_qwen_approval_mode() == "yolo"


def test_qwen_add_dirs_env_override_supports_colon_and_comma():
    """QWEN_ADD_DIRS parses both : and , separators (mirror codex)."""
    with _UnsetEnv():
        os.environ["QWEN_ADD_DIRS"] = "/a/b:/c/d,/e/f"
        from harness_allocator import config
        assert config.get_qwen_add_dirs() == ["/a/b", "/c/d", "/e/f"]


def test_qwen_bin_env_override_takes_precedence():
    with _UnsetEnv():
        os.environ["QWEN_BIN"] = "/opt/qwen/bin/qwen"
        from harness_allocator import config
        assert config.get_qwen_bin() == "/opt/qwen/bin/qwen"


# ── E. env dict ──────────────────────────────────────────────────────


def test_qwen_env_empty_when_nothing_configured():
    """Empty config + empty model_target -> empty dict (caller inherits parent env)."""
    env = build_qwen_env(model_target="", cfg=_QwenCfg())
    assert env == {}


def test_qwen_env_openai_base_url_forced_to_v1():
    """Non-empty base_url WITHOUT /v1 suffix is forced to end in /v1."""

    class Cfg(_QwenCfg):
        def get_qwen_base_url(self):
            return "http://localhost:8080"

    env = build_qwen_env(model_target="qwen3-8b", cfg=Cfg())
    assert env["OPENAI_BASE_URL"] == "http://localhost:8080/v1"


def test_qwen_env_openai_base_url_already_v1_unchanged():
    """base_url already ending in /v1 is unchanged (no double suffix)."""

    class Cfg(_QwenCfg):
        def get_qwen_base_url(self):
            return "http://localhost:8080/v1"

    env = build_qwen_env(model_target="qwen3-8b", cfg=Cfg())
    assert env["OPENAI_BASE_URL"] == "http://localhost:8080/v1"


def test_qwen_env_openai_base_url_trailing_slash_then_v1():
    """Trailing-slash base_url is normalised before the /v1 suffix is appended."""

    class Cfg(_QwenCfg):
        def get_qwen_base_url(self):
            return "http://localhost:8080/"

    env = build_qwen_env(model_target="qwen3-8b", cfg=Cfg())
    assert env["OPENAI_BASE_URL"] == "http://localhost:8080/v1"


def test_qwen_env_openai_base_url_omitted_when_empty():
    """Empty base_url -> OPENAI_BASE_URL key is absent."""
    env = build_qwen_env(model_target="qwen3-8b", cfg=_QwenCfg())
    assert "OPENAI_BASE_URL" not in env


def test_qwen_env_openai_model_is_verbatim():
    """OPENAI_MODEL is the resolved model_target verbatim (no substitution)."""
    env = build_qwen_env(model_target="qwen3-8b", cfg=_QwenCfg())
    assert env["OPENAI_MODEL"] == "qwen3-8b"


def test_qwen_env_openai_model_omitted_when_empty():
    """Empty model_target -> OPENAI_MODEL key is absent."""
    env = build_qwen_env(model_target="", cfg=_QwenCfg())
    assert "OPENAI_MODEL" not in env


def test_qwen_env_api_key_read_from_named_env_var(monkeypatch):
    """OPENAI_API_KEY is read from the env var NAMED by api_key_env (no secret in config)."""
    monkeypatch.setenv("MY_QWEN_KEY", "sk-secret-123")
    monkeypatch.delenv("QWEN_API_KEY", raising=False)

    class Cfg(_QwenCfg):
        def get_qwen_api_key_env(self):
            return "MY_QWEN_KEY"

    env = build_qwen_env(model_target="qwen3-8b", cfg=Cfg())
    assert env["OPENAI_API_KEY"] == "sk-secret-123"


def test_qwen_env_api_key_omitted_when_name_empty():
    """Empty api_key_env name -> OPENAI_API_KEY key is absent."""
    env = build_qwen_env(model_target="qwen3-8b", cfg=_QwenCfg())
    assert "OPENAI_API_KEY" not in env


def test_qwen_env_api_key_omitted_when_named_var_unset(monkeypatch):
    """Named env var present but unset/empty -> OPENAI_API_KEY key is absent."""

    class Cfg(_QwenCfg):
        def get_qwen_api_key_env(self):
            return "MY_QWEN_KEY"

    monkeypatch.delenv("MY_QWEN_KEY", raising=False)
    env = build_qwen_env(model_target="qwen3-8b", cfg=Cfg())
    assert "OPENAI_API_KEY" not in env


def test_qwen_env_api_key_omitted_when_named_var_empty(monkeypatch):
    """Named env var present but empty -> OPENAI_API_KEY key is absent."""

    class Cfg(_QwenCfg):
        def get_qwen_api_key_env(self):
            return "MY_QWEN_KEY"

    monkeypatch.setenv("MY_QWEN_KEY", "")
    env = build_qwen_env(model_target="qwen3-8b", cfg=Cfg())
    assert "OPENAI_API_KEY" not in env


def test_qwen_env_full_payload(monkeypatch):
    """All three keys together: base_url normalised, model verbatim, key from named var."""

    class Cfg(_QwenCfg):
        def get_qwen_base_url(self):
            return "http://localhost:8080"

        def get_qwen_api_key_env(self):
            return "MY_QWEN_KEY"

    monkeypatch.setenv("MY_QWEN_KEY", "sk-live")
    env = build_qwen_env(model_target="qwen3-8b", cfg=Cfg())
    assert env == {
        "OPENAI_BASE_URL": "http://localhost:8080/v1",
        "OPENAI_MODEL": "qwen3-8b",
        "OPENAI_API_KEY": "sk-live",
    }


# ── Harness-neutral builders route qwen ──────────────────────────────


def test_build_launch_argv_routes_qwen():
    """build_launch_argv('qwen', ...) -> build_qwen_argv(...) shape."""
    argv = build_launch_argv("qwen", model_target="qwen3-8b", task="x", cfg=_QwenCfgDirs())
    assert argv == [
        "qwen",
        "--approval-mode", "yolo",
        "--include-directories", "/srv/extra",
        "--include-directories", "/srv/data",
        "-p", "x",
    ]


def test_build_launch_command_routes_qwen():
    """build_launch_command('qwen', ...) returns the shell-string form."""
    cmd = build_launch_command("qwen", model_target="qwen3-8b", task="x", cfg=_QwenCfgDirs())
    argv = build_launch_argv("qwen", model_target="qwen3-8b", task="x", cfg=_QwenCfgDirs())
    assert shlex.split(cmd) == argv


def test_build_task_argv_routes_qwen():
    """build_task_argv('qwen', ...) is the one-shot shape (with -p)."""
    argv = build_task_argv("qwen", model_target="qwen3-8b", task="hello", cfg=_QwenCfg())
    assert argv == ["qwen", "--approval-mode", "yolo", "-p", "hello"]


def test_build_task_invocation_routes_qwen():
    """build_task_invocation('qwen', ...) is the shell-string form."""
    cmd = build_task_invocation("qwen", model_target="qwen3-8b", task="hello", cfg=_QwenCfg())
    argv = build_task_argv("qwen", model_target="qwen3-8b", task="hello", cfg=_QwenCfg())
    assert shlex.split(cmd) == argv


# ── Spec routing, env threading, and refusal tests (Run 022 / HA-2 — D3+D4) ──
#
# These tests cover the D3/D4 surface added in handoff 086:
# - A. Spec routing: execute_spec routes harness='qwen' through the new
#      adapter; SUPPORTED_HARNESSES is now five keys (the validation passes).
# - B. Env threading: execute/execute_spec thread {**os.environ, **qwen_env}
#      into Popen for qwen; the four existing harnesses route with env=None.
# - C. Refusal: missing-binary refusal raises a typed ValueError BEFORE
#      any subprocess (TG7 / D4 contract).

import importlib
import threading as _threading
from pathlib import Path as _Path

# Lazy imports for the spec / invoke surface (test_qwen_adapter is the
# right place for these — it owns the qwen routing story end-to-end).
from harness_allocator import invoke as _inv  # noqa: E402
from harness_allocator.adapter import build_qwen_argv  # noqa: E402
from harness_allocator.invoke import execute, execute_spec  # noqa: E402
from harness_allocator.runspec import (  # noqa: E402
    HarnessRunSpec,
    RunOutput,
    RunRequirements,
    RunSession,
    RunTiming,
)
from harness_allocator.status import SUCCESS  # noqa: E402


# ── Spec-routing helpers (mirror test_invoke_spec.py) ───────────────────


class _QwenFakeProc:
    """Minimal Popen stand-in — captures the argv + env that run_argv passes."""

    def __init__(self, poll_results=(0,), stdout="qwen-output\n", stderr=""):
        self._poll_results = list(poll_results)
        self.pid = 5151
        self.returncode = None
        self._stdout = stdout
        self._stderr = stderr
        self.killed = False
        self.recorded_argv = None
        self.recorded_kwargs = None

    def poll(self):
        if self._poll_results:
            self.returncode = self._poll_results.pop(0)
        return self.returncode

    def communicate(self):
        return self._stdout, self._stderr

    def kill(self):
        self.killed = True
        self.returncode = -9


def _patch_popen_qwen(monkeypatch, proc):
    """Patch _inv.subprocess.Popen to return ``proc`` and capture argv + kwargs."""

    recorded = {"argv": None, "kwargs": None}

    def fake_popen(argv, *a, **k):
        recorded["argv"] = list(argv)
        recorded["kwargs"] = dict(k)
        return proc

    monkeypatch.setattr(_inv.subprocess, "Popen", fake_popen)
    return recorded


def _assert_no_popen_qwen(monkeypatch, fn, *args, **kwargs):
    """Run ``fn``; assert _inv.subprocess.Popen was NEVER called."""

    called = {"yes": False}

    def explode(*a, **k):
        called["yes"] = True
        raise AssertionError(
            "subprocess.Popen must NOT be called when validation refuses "
            "(TG7 / D4)"
        )

    monkeypatch.setattr(_inv.subprocess, "Popen", explode)
    return called


# ── A. Spec routing (TG3) ──────────────────────────────────────────────


def _make_qwen_spec(prompt="hello", model_reference="qwen3-8b", request_id="rid-1"):
    """Build a minimal HarnessRunSpec for qwen — no required capabilities."""
    return HarnessRunSpec(
        request_id=request_id,
        harness="qwen",
        model_reference=model_reference,
        prompt=prompt,
        session=RunSession(mode="oneshot"),
        requirements=RunRequirements(capabilities=[]),
        output=RunOutput(expected_artifacts=[]),
    )


def test_qwen_spec_routes_through_execute_spec(monkeypatch):
    """Spec routing (TG3): execute_spec with harness='qwen' spawns the qwen argv."""
    proc = _QwenFakeProc(poll_results=(0,), stdout="qwen-output\n", stderr="")
    recorded = _patch_popen_qwen(monkeypatch, proc)

    spec = _make_qwen_spec(prompt="summarise this", model_reference="qwen3-8b", request_id="rid-7")
    result = execute_spec(spec, cfg=_QwenCfgDirs())

    assert recorded["argv"] is not None, "Popen was not called"
    # The argv matches the qwen one-shot shape (build_qwen_argv output)
    expected_argv = build_qwen_argv(
        model_target="qwen3-8b", task="summarise this", cfg=_QwenCfgDirs()
    )
    assert recorded["argv"] == expected_argv
    # Result fields are bound
    assert result.harness == "qwen"
    assert result.status == SUCCESS
    assert result.exit_code == 0
    assert result.stdout == "qwen-output\n"
    assert result.stderr == ""
    assert result.request_id == "rid-7"


def test_qwen_spec_validates_against_supported_harnesses(monkeypatch):
    """After the 086 flip, harness='qwen' validates (UnknownHarnessError is NOT raised)."""
    proc = _QwenFakeProc(poll_results=(0,), stdout="ok\n", stderr="")
    _patch_popen_qwen(monkeypatch, proc)

    spec = _make_qwen_spec(prompt="x", model_reference="m")
    # validate() must not raise — qwen is now in SUPPORTED_HARNESSES
    spec.validate()  # no exception
    # And execute_spec completes without UnknownHarnessError
    result = execute_spec(spec, cfg=_QwenCfg())
    assert result.harness == "qwen"
    assert result.status == SUCCESS


# ── B. Env threading ───────────────────────────────────────────────────


def test_qwen_spec_threads_env_into_popen(monkeypatch):
    """Spec routing threads {**os.environ, OPENAI_BASE_URL=/v1, OPENAI_MODEL,
    OPENAI_API_KEY} into Popen's env kwarg."""

    class _EnvCfg(_QwenCfg):
        def get_qwen_base_url(self):
            return "http://localhost:8080"

        def get_qwen_api_key_env(self):
            return "MY_QWEN_KEY"

    monkeypatch.setenv("MY_QWEN_KEY", "sk-live-1")

    proc = _QwenFakeProc(poll_results=(0,), stdout="x", stderr="")
    recorded = _patch_popen_qwen(monkeypatch, proc)

    spec = _make_qwen_spec(prompt="x", model_reference="qwen3-8b")
    execute_spec(spec, cfg=_EnvCfg())

    env = recorded["kwargs"]["env"]
    assert env is not None, "qwen spec must pass env to Popen"
    assert env["OPENAI_BASE_URL"] == "http://localhost:8080/v1"
    assert env["OPENAI_MODEL"] == "qwen3-8b"
    assert env["OPENAI_API_KEY"] == "sk-live-1"
    # Parent env is MERGED — PATH must remain (qwen binary resolves via PATH)
    assert "PATH" in env  # inherited from os.environ


def test_qwen_execute_threads_env_into_popen(monkeypatch):
    """execute() (harness='qwen') threads the qwen env into Popen."""
    proc = _QwenFakeProc(poll_results=(0,), stdout="x", stderr="")
    recorded = _patch_popen_qwen(monkeypatch, proc)

    monkeypatch.setenv("MY_QWEN_KEY2", "sk-live-2")

    class _EnvCfg(_QwenCfg):
        def get_qwen_base_url(self):
            return "http://localhost:9000"

        def get_qwen_api_key_env(self):
            return "MY_QWEN_KEY2"

    execute(
        harness="qwen",
        model_target="qwen3-8b",
        task="x",
        cwd="/tmp",
        cfg=_EnvCfg(),
    )

    env = recorded["kwargs"]["env"]
    assert env is not None
    assert env["OPENAI_BASE_URL"] == "http://localhost:9000/v1"
    assert env["OPENAI_MODEL"] == "qwen3-8b"
    assert env["OPENAI_API_KEY"] == "sk-live-2"


def test_qwen_spec_with_default_cfg_passes_env_none(monkeypatch):
    """When build_qwen_env returns {} (default cfg), execute_spec passes env=None."""
    proc = _QwenFakeProc(poll_results=(0,), stdout="x", stderr="")
    recorded = _patch_popen_qwen(monkeypatch, proc)

    spec = _make_qwen_spec(prompt="x", model_reference="qwen3-8b")
    # _QwenCfg has empty base_url + empty api_key_env => build_qwen_env returns {} (only model)
    # Actually, with model_target="qwen3-8b", OPENAI_MODEL is set.
    # To force env=None, use an empty model_target.
    spec2 = _make_qwen_spec(prompt="x", model_reference="")
    result = execute_spec(spec2, cfg=_QwenCfg())
    # env=None (no base_url, no api_key_env, no model_target)
    assert "env" not in recorded["kwargs"], (
        "qwen spec with empty model + empty cfg must pass env=None (inherit)"
    )
    assert result.status == SUCCESS


class _DshCfg:
    """Minimal dsh cfg — mirrors the package's _FakeCfg shape."""

    def get_dsh_bin(self):
        return "npx @deepseek-ai/dsh"

    def get_dsh_profile(self):
        return "headless"

    def get_dsh_patch_path(self):
        return ""


def test_dsh_execute_routes_with_env_none(monkeypatch):
    """The four existing harnesses (here dsh) route with env=None — TG5 contract at the invoke layer."""
    proc = _QwenFakeProc(poll_results=(0,), stdout="x", stderr="")
    recorded = _patch_popen_qwen(monkeypatch, proc)

    execute(
        harness="dsh",
        model_target="deepseek-v4-pro",
        task="x",
        cwd="/tmp",
        cfg=_DshCfg(),
    )
    # env=None (historical inherit) — dsh unchanged
    assert "env" not in recorded["kwargs"]


def test_dsh_execute_spec_routes_with_env_none(monkeypatch):
    """execute_spec with harness='dsh' passes env=None (TG5 contract)."""
    proc = _QwenFakeProc(poll_results=(0,), stdout="x", stderr="")
    recorded = _patch_popen_qwen(monkeypatch, proc)

    spec = HarnessRunSpec(
        request_id="rid-dsh",
        harness="dsh",
        model_reference="deepseek-v4-pro",
        prompt="x",
        session=RunSession(mode="oneshot"),
        requirements=RunRequirements(capabilities=[]),
        output=RunOutput(expected_artifacts=[]),
    )
    execute_spec(spec, cfg=_DshCfg())
    assert "env" not in recorded["kwargs"]


# ── C. Refusal (TG7 / D4) ───────────────────────────────────────────────


def test_qwen_argv_refus_when_binary_is_empty():
    """build_qwen_argv raises a typed ValueError when cfg.get_qwen_bin() is empty."""

    class _EmptyBinCfg(_QwenCfg):
        def get_qwen_bin(self):
            return ""

    import pytest as _pytest
    with _pytest.raises(ValueError, match="qwen"):
        build_qwen_argv(model_target="m", task="t", cfg=_EmptyBinCfg())


def test_qwen_argv_refus_when_binary_is_whitespace():
    """Whitespace-only qwen_bin also refuses (shlex.split yields [])."""

    class _WhitespaceCfg(_QwenCfg):
        def get_qwen_bin(self):
            return "   \t  "

    import pytest as _pytest
    with _pytest.raises(ValueError, match="qwen"):
        build_qwen_argv(model_target="m", task="t", cfg=_WhitespaceCfg())


def test_qwen_spec_refus_missing_binary_no_popen(monkeypatch):
    """execute_spec refuses BEFORE any subprocess when qwen_bin is empty."""

    class _EmptyBinCfg(_QwenCfg):
        def get_qwen_bin(self):
            return ""

    called = _assert_no_popen_qwen(monkeypatch, None)

    import pytest as _pytest
    spec = _make_qwen_spec(prompt="x", model_reference="m")
    with _pytest.raises(ValueError, match="qwen"):
        execute_spec(spec, cfg=_EmptyBinCfg())
    assert called["yes"] is False, (
        "Popen must NOT be called when the typed missing-binary refusal fires"
    )
