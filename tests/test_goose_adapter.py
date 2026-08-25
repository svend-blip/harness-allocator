"""Tests for the ``goose`` adapter (Run 026 / HA-3 — D1 + D2 + D4 core).

Contract (GOAL.md Run 026 §1 D1/D2 and the binding constraints):

- ``build_goose_argv`` returns ``[<goose_bin>, run, --no-session, -q,
  --max-turns, 1, -t, <task>]``: model, provider, endpoint and api key
  NEVER in argv; ``-t <task>`` is appended only when a task is supplied.
  The bound argv is grounded in the installed goose 1.47.0 build
  (``goose run --help``).
- ``build_goose_invocation`` matches argv joined (mirrors dsh/qwen).
- ``build_goose_env`` carries ``GOOSE_PROVIDER`` (set when an
  OpenAI-specific knob is configured), ``OPENAI_BASE_URL`` (forced /v1),
  ``GOOSE_MODEL`` (verbatim, NOT ``OPENAI_MODEL``), and ``OPENAI_API_KEY``
  (read from the NAMED env var). Empty config + empty model -> empty
  dict (caller inherits parent env).
- ``get_capabilities("goose")`` has EXACTLY the five groups with
  ``sessions.mode == "oneshot"``, ``execution.headless == True``,
  ``automation.non_interactive == True``, and extensions
  ``{skills: True, mcp: True, custom_tools: True}`` (the EXTENSIBLE
  surface — measured honestly from the build).
- Config defaults: each ``get_goose_*`` returns its documented default
  when the env is cleared and no ini section exists.

Stdlib + pytest only (the package's standing constraint). The goose CLI
is a pinned system tool (1.47.0) — we do NOT execute it here. No
spec-routing tests here (handoff 2 / D3 owns ``-k spec``), no
``*refus*`` refusal tests beyond the missing-binary refusal (handoff 2
owns the routing refusal), no enumeration test edits (handoff 2 owns the
SUPPORTED_HARNESSES five→six flip — this handoff's contract leaves
SUPPORTED_HARNESSES at five keys so the qwen / dsh / codex / claude-code
/ opencode goldens stay byte-identical).

The four existing harnesses' behaviour is intentionally untouched — see
test_harness_allocator.py, test_qwen_adapter.py, test_capabilities.py
for those.
"""

import os
import shlex
import sys
from pathlib import Path

import pytest

# Import the package from the project root (sibling of tests/).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness_allocator.adapter import (  # noqa: E402
    build_goose_argv,
    build_goose_env,
    build_goose_invocation,
    build_launch_argv,
    build_launch_command,
    build_task_argv,
    build_task_invocation,
)
from harness_allocator.capabilities import get_capabilities  # noqa: E402


# ── Test doubles (mirror the package's _FakeCfg / _UnsetEnv style) ────


class _GooseCfg:
    """Fixed cfg so argv/env assertions are byte-stable.

    Mirrors ``tests/test_qwen_adapter.py::_QwenCfg`` for the goose
    surface: bin = "goose", no base_url, no api_key_env, no workdir,
    no add_dirs.
    """

    def get_goose_bin(self):
        return "goose"

    def get_goose_base_url(self):
        return ""

    def get_goose_api_key_env(self):
        return ""

    def get_goose_workdir(self):
        return ""

    def get_goose_add_dirs(self):
        return []


class _GooseCfgDirs(_GooseCfg):
    """Cfg variant that supplies a couple of add-dirs (no-op for argv)."""

    def get_goose_add_dirs(self):
        return ["/srv/extra", "/srv/data"]


class _GooseCfgBaseUrl(_GooseCfg):
    """Cfg variant with a base URL (forces /v1 in env)."""

    def get_goose_base_url(self):
        return "http://localhost:8080"


class _UnsetEnv:
    """Context manager: clear GOOSE_* env vars and restore prior values."""

    def __init__(self):
        self._keys = (
            "GOOSE_BIN",
            "GOOSE_BASE_URL",
            "GOOSE_API_KEY_ENV",
            "GOOSE_WORKDIR",
            "GOOSE_ADD_DIRS",
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


def test_goose_argv_golden_minimum():
    """Default cfg + no task -> [goose, run, --no-session, -q, --max-turns, 1]."""
    argv = build_goose_argv(model_target="qwen3-8b", cfg=_GooseCfg())
    assert argv == ["goose", "run", "--no-session", "-q", "--max-turns", "1"]


def test_goose_argv_golden_with_task():
    """With task: the -t <task> pair is appended at the end."""
    argv = build_goose_argv(
        model_target="qwen3-8b", task="summarise this", cfg=_GooseCfg()
    )
    assert argv == [
        "goose", "run", "--no-session", "-q", "--max-turns", "1",
        "-t", "summarise this",
    ]


def test_goose_argv_omits_model_target():
    """The model travels in the env (GOOSE_MODEL), NEVER in argv."""
    argv = build_goose_argv(model_target="some-other-model", task="x", cfg=_GooseCfg())
    assert "some-other-model" not in argv
    assert "qwen3-8b" not in argv


def test_goose_argv_task_is_single_element_with_newlines():
    """The full task is one argv element with embedded newlines preserved."""
    task = "Reply with exactly: OK\nSecond line\nThird line\nFinal"
    argv = build_goose_argv(model_target="qwen3-8b", task=task, cfg=_GooseCfg())
    assert argv[-2:] == ["-t", task]
    assert argv[-1] == task
    assert argv[-1].count("\n") == 3


def test_goose_argv_task_omitted_when_empty():
    """No task -> -t flag is absent entirely (launch form)."""
    argv = build_goose_argv(model_target="qwen3-8b", cfg=_GooseCfg())
    assert "-t" not in argv


def test_goose_argv_no_include_directories_flag():
    """Goose's CLI has NO --include-directories flag; add_dirs are no-op in argv."""
    argv = build_goose_argv(
        model_target="qwen3-8b", task="x", cfg=_GooseCfgDirs()
    )
    assert "--include-directories" not in argv
    # The add_dirs dirs themselves must NOT appear in argv.
    assert "/srv/extra" not in argv
    assert "/srv/data" not in argv


def test_goose_argv_non_interactive_flags_present():
    """The bound non-interactive surface (--no-session -q --max-turns 1) is ALWAYS set."""
    argv = build_goose_argv(model_target="qwen3-8b", task="x", cfg=_GooseCfg())
    assert "--no-session" in argv
    assert "-q" in argv
    assert "--max-turns" in argv
    assert "1" in argv
    # All three come right after `run` and BEFORE -t.
    assert argv[1:6] == ["run", "--no-session", "-q", "--max-turns", "1"]


def test_goose_argv_bin_supports_full_launcher_path():
    """bin may be a multi-token launcher (shlex-split, like qwen / dsh)."""

    class Cfg(_GooseCfg):
        def get_goose_bin(self):
            return "/opt/goose/bin/goose --extra"

    argv = build_goose_argv(model_target="qwen3-8b", cfg=Cfg())
    # The shlex-split bin becomes the leading argv tokens.
    assert argv[0] == "/opt/goose/bin/goose"
    assert argv[1] == "--extra"
    assert argv[2] == "run"


# ── B. build_goose_invocation matches argv joined ──────────────────────


def test_goose_invocation_matches_argv_joined():
    """The shell command, when split, reconstructs the exact argv list."""
    task = "line one\nline two\nSupervisor"
    cmd = build_goose_invocation(model_target="qwen3-8b", task=task, cfg=_GooseCfg())
    argv = build_goose_argv(model_target="qwen3-8b", task=task, cfg=_GooseCfg())
    assert shlex.split(cmd) == argv


def test_goose_invocation_launch_form():
    """No task -> the invocation is the launch form (no -t <task> token)."""
    cmd = build_goose_invocation(model_target="qwen3-8b", cfg=_GooseCfg())
    argv = build_goose_argv(model_target="qwen3-8b", cfg=_GooseCfg())
    assert shlex.split(cmd) == argv
    # The "-t" flag is the task argument; "--max-turns" contains "-t" as a
    # substring, so a literal ``"-t" not in cmd`` check is a false positive.
    # Token-bound check: no standalone "-t" element.
    assert "-t" not in shlex.split(cmd)


# ── C. manifest shape ────────────────────────────────────────────────


def test_goose_manifest_has_exactly_five_groups():
    """get_capabilities("goose") returns EXACTLY the five contract groups."""
    manifest = get_capabilities("goose")
    expected = {"execution", "workspace", "sessions", "extensions", "automation"}
    assert set(manifest.keys()) == expected


def test_goose_manifest_sessions_mode_is_oneshot():
    """sessions.mode == "oneshot" is BOUND (Run 026 §1 D1; headless --no-session)."""
    manifest = get_capabilities("goose")
    assert manifest["sessions"]["mode"] == "oneshot"


def test_goose_manifest_sessions_resume_is_false():
    """sessions.session_resume == False (--no-session always set, no resume)."""
    manifest = get_capabilities("goose")
    assert manifest["sessions"]["session_resume"] is False
    assert manifest["sessions"]["persistent_session"] is False


def test_goose_manifest_execution_headless_is_true():
    """execution.headless == True (goose run is the headless one-shot)."""
    manifest = get_capabilities("goose")
    assert manifest["execution"]["headless"] is True
    assert manifest["execution"]["terminal"] is False
    assert manifest["execution"]["interactive"] is False


def test_goose_manifest_automation_non_interactive_is_true():
    """automation.non_interactive == True (bound surface --no-session -q --max-turns 1)."""
    manifest = get_capabilities("goose")
    assert manifest["automation"]["non_interactive"] is True
    assert manifest["automation"]["deterministic_exit"] is True
    assert manifest["automation"]["interrupt_safe"] is True


def test_goose_manifest_extensions_skills_is_true():
    """extensions.skills == True (goose skills subcommand)."""
    manifest = get_capabilities("goose")
    assert manifest["extensions"]["skills"] is True


def test_goose_manifest_extensions_mcp_is_true():
    """extensions.mcp == True (goose mcp subcommand + --with-streamable-http-extension)."""
    manifest = get_capabilities("goose")
    assert manifest["extensions"]["mcp"] is True


def test_goose_manifest_extensions_custom_tools_is_true():
    """extensions.custom_tools == True (goose is the first EXTENSIBLE harness)."""
    manifest = get_capabilities("goose")
    assert manifest["extensions"]["custom_tools"] is True


def test_goose_manifest_returns_fresh_copy():
    """Mutating the returned manifest must not affect the next call."""
    m1 = get_capabilities("goose")
    m1["execution"]["headless"] = False
    m2 = get_capabilities("goose")
    assert m2["execution"]["headless"] is True


# ── D. config defaults ───────────────────────────────────────────────


def test_goose_bin_default_when_env_cleared_and_no_ini():
    with _UnsetEnv():
        from harness_allocator import config
        assert config.get_goose_bin() == "goose"


def test_goose_base_url_default_is_empty():
    with _UnsetEnv():
        from harness_allocator import config
        assert config.get_goose_base_url() == ""


def test_goose_api_key_env_default_is_empty():
    with _UnsetEnv():
        from harness_allocator import config
        assert config.get_goose_api_key_env() == ""


def test_goose_workdir_default_is_empty():
    with _UnsetEnv():
        from harness_allocator import config
        assert config.get_goose_workdir() == ""


def test_goose_add_dirs_default_is_empty_list():
    with _UnsetEnv():
        from harness_allocator import config
        assert config.get_goose_add_dirs() == []


def test_goose_add_dirs_env_override_supports_colon_and_comma():
    """GOOSE_ADD_DIRS parses both : and , separators (mirror codex / qwen)."""
    with _UnsetEnv():
        os.environ["GOOSE_ADD_DIRS"] = "/a/b:/c/d,/e/f"
        from harness_allocator import config
        assert config.get_goose_add_dirs() == ["/a/b", "/c/d", "/e/f"]


def test_goose_bin_env_override_takes_precedence():
    with _UnsetEnv():
        os.environ["GOOSE_BIN"] = "/opt/goose/bin/goose"
        from harness_allocator import config
        assert config.get_goose_bin() == "/opt/goose/bin/goose"


def test_goose_base_url_env_override_takes_precedence():
    with _UnsetEnv():
        os.environ["GOOSE_BASE_URL"] = "http://example.test:9000"
        from harness_allocator import config
        assert config.get_goose_base_url() == "http://example.test:9000"


def test_goose_api_key_env_env_override_takes_precedence():
    with _UnsetEnv():
        os.environ["GOOSE_API_KEY_ENV"] = "MY_GOOSE_KEY"
        from harness_allocator import config
        assert config.get_goose_api_key_env() == "MY_GOOSE_KEY"


# ── E. env dict ──────────────────────────────────────────────────────


def test_goose_env_empty_when_nothing_configured():
    """Empty config + empty model_target -> empty dict (caller inherits parent env)."""
    env = build_goose_env(model_target="", cfg=_GooseCfg())
    assert env == {}


def test_goose_env_goose_provider_set_when_base_url_set():
    """GOOSE_PROVIDER is set when an OpenAI-specific knob (base_url) is configured."""

    class Cfg(_GooseCfg):
        def get_goose_base_url(self):
            return "http://localhost:8080"

    env = build_goose_env(model_target="qwen3-8b", cfg=Cfg())
    assert env["GOOSE_PROVIDER"] == "openai"


def test_goose_env_goose_provider_set_when_api_key_env_set(monkeypatch):
    """GOOSE_PROVIDER is set when api_key_env is configured (regardless of value)."""

    class Cfg(_GooseCfg):
        def get_goose_api_key_env(self):
            return "MY_GOOSE_KEY"

    monkeypatch.delenv("MY_GOOSE_KEY", raising=False)
    env = build_goose_env(model_target="", cfg=Cfg())
    assert env["GOOSE_PROVIDER"] == "openai"
    # But api_key itself is omitted (named env var unset).
    assert "OPENAI_API_KEY" not in env


def test_goose_env_goose_provider_omitted_when_only_model():
    """GOOSE_PROVIDER is NOT set when ONLY model is configured (model alone is provider-neutral)."""
    env = build_goose_env(model_target="qwen3-8b", cfg=_GooseCfg())
    assert "GOOSE_PROVIDER" not in env
    assert env["GOOSE_MODEL"] == "qwen3-8b"


def test_goose_env_openai_base_url_forced_to_v1():
    """Non-empty base_url WITHOUT /v1 suffix is forced to end in /v1."""

    class Cfg(_GooseCfg):
        def get_goose_base_url(self):
            return "http://localhost:8080"

    env = build_goose_env(model_target="qwen3-8b", cfg=Cfg())
    assert env["OPENAI_BASE_URL"] == "http://localhost:8080/v1"


def test_goose_env_openai_base_url_already_v1_unchanged():
    """base_url already ending in /v1 is unchanged (no double suffix)."""

    class Cfg(_GooseCfg):
        def get_goose_base_url(self):
            return "http://localhost:8080/v1"

    env = build_goose_env(model_target="qwen3-8b", cfg=Cfg())
    assert env["OPENAI_BASE_URL"] == "http://localhost:8080/v1"


def test_goose_env_openai_base_url_trailing_slash_then_v1():
    """Trailing-slash base_url is normalised before the /v1 suffix is appended."""

    class Cfg(_GooseCfg):
        def get_goose_base_url(self):
            return "http://localhost:8080/"

    env = build_goose_env(model_target="qwen3-8b", cfg=Cfg())
    assert env["OPENAI_BASE_URL"] == "http://localhost:8080/v1"


def test_goose_env_openai_base_url_omitted_when_empty():
    """Empty base_url -> OPENAI_BASE_URL key is absent."""
    env = build_goose_env(model_target="qwen3-8b", cfg=_GooseCfg())
    assert "OPENAI_BASE_URL" not in env


def test_goose_env_goose_model_is_verbatim():
    """GOOSE_MODEL is the resolved model_target verbatim (no substitution)."""
    env = build_goose_env(model_target="qwen3-8b", cfg=_GooseCfg())
    assert env["GOOSE_MODEL"] == "qwen3-8b"


def test_goose_env_uses_goose_model_not_openai_model():
    """GOOSE_MODEL is the env var name (NOT OPENAI_MODEL — goose rejects the latter)."""
    env = build_goose_env(model_target="qwen3-8b", cfg=_GooseCfg())
    assert "GOOSE_MODEL" in env
    assert "OPENAI_MODEL" not in env


def test_goose_env_goose_model_omitted_when_empty():
    """Empty model_target -> GOOSE_MODEL key is absent."""
    env = build_goose_env(model_target="", cfg=_GooseCfg())
    assert "GOOSE_MODEL" not in env


def test_goose_env_api_key_read_from_named_env_var(monkeypatch):
    """OPENAI_API_KEY is read from the env var NAMED by api_key_env (no secret in config)."""
    monkeypatch.setenv("MY_GOOSE_KEY", "sk-secret-123")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    class Cfg(_GooseCfg):
        def get_goose_api_key_env(self):
            return "MY_GOOSE_KEY"

    env = build_goose_env(model_target="qwen3-8b", cfg=Cfg())
    assert env["OPENAI_API_KEY"] == "sk-secret-123"


def test_goose_env_api_key_omitted_when_name_empty():
    """Empty api_key_env name -> OPENAI_API_KEY key is absent."""
    env = build_goose_env(model_target="qwen3-8b", cfg=_GooseCfg())
    assert "OPENAI_API_KEY" not in env


def test_goose_env_api_key_omitted_when_named_var_unset(monkeypatch):
    """Named env var present but unset/empty -> OPENAI_API_KEY key is absent."""

    class Cfg(_GooseCfg):
        def get_goose_api_key_env(self):
            return "MY_GOOSE_KEY"

    monkeypatch.delenv("MY_GOOSE_KEY", raising=False)
    env = build_goose_env(model_target="qwen3-8b", cfg=Cfg())
    assert "OPENAI_API_KEY" not in env


def test_goose_env_api_key_omitted_when_named_var_empty(monkeypatch):
    """Named env var present but empty -> OPENAI_API_KEY key is absent."""

    class Cfg(_GooseCfg):
        def get_goose_api_key_env(self):
            return "MY_GOOSE_KEY"

    monkeypatch.setenv("MY_GOOSE_KEY", "")
    env = build_goose_env(model_target="qwen3-8b", cfg=Cfg())
    assert "OPENAI_API_KEY" not in env


def test_goose_env_full_payload(monkeypatch):
    """All four keys together: provider set, base_url normalised, model verbatim, key from named var."""

    class Cfg(_GooseCfg):
        def get_goose_base_url(self):
            return "http://localhost:8080"

        def get_goose_api_key_env(self):
            return "MY_GOOSE_KEY"

    monkeypatch.setenv("MY_GOOSE_KEY", "sk-live")
    env = build_goose_env(model_target="qwen3-8b", cfg=Cfg())
    assert env == {
        "GOOSE_PROVIDER": "openai",
        "OPENAI_BASE_URL": "http://localhost:8080/v1",
        "GOOSE_MODEL": "qwen3-8b",
        "OPENAI_API_KEY": "sk-live",
    }


# ── Harness-neutral builders route goose ──────────────────────────────


def test_build_launch_argv_routes_goose():
    """build_launch_argv('goose', ...) -> build_goose_argv(...) shape."""
    argv = build_launch_argv("goose", model_target="qwen3-8b", task="x", cfg=_GooseCfg())
    assert argv == [
        "goose", "run", "--no-session", "-q", "--max-turns", "1",
        "-t", "x",
    ]


def test_build_launch_command_routes_goose():
    """build_launch_command('goose', ...) returns the shell-string form."""
    cmd = build_launch_command("goose", model_target="qwen3-8b", task="x", cfg=_GooseCfg())
    argv = build_launch_argv("goose", model_target="qwen3-8b", task="x", cfg=_GooseCfg())
    assert shlex.split(cmd) == argv


def test_build_task_argv_routes_goose():
    """build_task_argv('goose', ...) is the one-shot shape (with -t)."""
    argv = build_task_argv("goose", model_target="qwen3-8b", task="hello", cfg=_GooseCfg())
    assert argv == [
        "goose", "run", "--no-session", "-q", "--max-turns", "1",
        "-t", "hello",
    ]


def test_build_task_invocation_routes_goose():
    """build_task_invocation('goose', ...) is the shell-string form."""
    cmd = build_task_invocation("goose", model_target="qwen3-8b", task="hello", cfg=_GooseCfg())
    argv = build_task_argv("goose", model_target="qwen3-8b", task="hello", cfg=_GooseCfg())
    assert shlex.split(cmd) == argv


# ── Missing-binary refusal (TG7 / D4 contract — mirror build_qwen_argv) ──


def test_goose_argv_refus_when_binary_is_empty():
    """build_goose_argv raises a typed ValueError when cfg.get_goose_bin() is empty."""

    class _EmptyBinCfg(_GooseCfg):
        def get_goose_bin(self):
            return ""

    with pytest.raises(ValueError, match="goose"):
        build_goose_argv(model_target="m", task="t", cfg=_EmptyBinCfg())


def test_goose_argv_refus_when_binary_is_whitespace():
    """Whitespace-only goose_bin also refuses (shlex.split yields [])."""

    class _WhitespaceCfg(_GooseCfg):
        def get_goose_bin(self):
            return "   \t  "

    with pytest.raises(ValueError, match="goose"):
        build_goose_argv(model_target="m", task="t", cfg=_WhitespaceCfg())


# ── Spec routing, env threading, and refusal tests (Run 026 / HA-3 — D3+D4) ──
#
# These tests cover the D3/D4 surface added in handoff 122:
# - A. Spec routing: execute_spec routes harness='goose' through the new
#      adapter; SUPPORTED_HARNESSES is now six keys (the validation passes).
# - B. Env threading: execute/execute_spec thread {**os.environ, **goose_env}
#      into Popen for goose; the four existing harnesses route with env=None.
# - C. Refusal: missing-binary refusal raises a typed ValueError BEFORE
#      any subprocess (TG7 / D4 contract — mirror build_qwen_argv).

from harness_allocator import invoke as _inv  # noqa: E402
from harness_allocator.adapter import build_goose_argv  # noqa: E402
from harness_allocator.invoke import execute, execute_spec  # noqa: E402
from harness_allocator.runspec import (  # noqa: E402
    HarnessRunSpec,
    RunOutput,
    RunRequirements,
    RunSession,
)
from harness_allocator.status import SUCCESS  # noqa: E402


# ── Spec-routing helpers (mirror test_qwen_adapter.py) ───────────────────


class _GooseFakeProc:
    """Minimal Popen stand-in — captures the argv + env that run_argv passes."""

    def __init__(self, poll_results=(0,), stdout="goose-output\n", stderr=""):
        self._poll_results = list(poll_results)
        self.pid = 6161
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


def _patch_popen_goose(monkeypatch, proc):
    """Patch _inv.subprocess.Popen to return ``proc`` and capture argv + kwargs."""

    recorded = {"argv": None, "kwargs": None}

    def fake_popen(argv, *a, **k):
        recorded["argv"] = list(argv)
        recorded["kwargs"] = dict(k)
        return proc

    monkeypatch.setattr(_inv.subprocess, "Popen", fake_popen)
    return recorded


def _assert_no_popen_goose(monkeypatch, fn, *args, **kwargs):
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


def _make_goose_spec(prompt="hello", model_reference="qwen3-8b", request_id="rid-1"):
    """Build a minimal HarnessRunSpec for goose — no required capabilities."""
    return HarnessRunSpec(
        request_id=request_id,
        harness="goose",
        model_reference=model_reference,
        prompt=prompt,
        session=RunSession(mode="oneshot"),
        requirements=RunRequirements(capabilities=[]),
        output=RunOutput(expected_artifacts=[]),
    )


def test_goose_spec_routes_through_execute_spec(monkeypatch):
    """Spec routing (TG3): execute_spec with harness='goose' spawns the goose argv."""
    proc = _GooseFakeProc(poll_results=(0,), stdout="goose-output\n", stderr="")
    recorded = _patch_popen_goose(monkeypatch, proc)

    spec = _make_goose_spec(prompt="summarise this", model_reference="qwen3-8b", request_id="rid-7")
    result = execute_spec(spec, cfg=_GooseCfgDirs())

    assert recorded["argv"] is not None, "Popen was not called"
    # The argv matches the goose one-shot shape (build_goose_argv output)
    expected_argv = build_goose_argv(
        model_target="qwen3-8b", task="summarise this", cfg=_GooseCfgDirs()
    )
    assert recorded["argv"] == expected_argv
    # Result fields are bound
    assert result.harness == "goose"
    assert result.status == SUCCESS
    assert result.exit_code == 0
    assert result.stdout == "goose-output\n"
    assert result.stderr == ""
    assert result.request_id == "rid-7"


def test_goose_spec_validates_against_supported_harnesses(monkeypatch):
    """After the 122 flip, harness='goose' validates (UnknownHarnessError is NOT raised)."""
    proc = _GooseFakeProc(poll_results=(0,), stdout="ok\n", stderr="")
    _patch_popen_goose(monkeypatch, proc)

    spec = _make_goose_spec(prompt="x", model_reference="m")
    # validate() must not raise — goose is now in SUPPORTED_HARNESSES
    spec.validate()  # no exception
    # And execute_spec completes without UnknownHarnessError
    result = execute_spec(spec, cfg=_GooseCfg())
    assert result.harness == "goose"
    assert result.status == SUCCESS


# ── B. Env threading ───────────────────────────────────────────────────


def test_goose_spec_threads_env_into_popen(monkeypatch):
    """Spec routing threads {**os.environ, GOOSE_PROVIDER, OPENAI_BASE_URL=/v1,
    GOOSE_MODEL, OPENAI_API_KEY} into Popen's env kwarg."""

    class _EnvCfg(_GooseCfg):
        def get_goose_base_url(self):
            return "http://localhost:8080"

        def get_goose_api_key_env(self):
            return "MY_GOOSE_KEY"

    monkeypatch.setenv("MY_GOOSE_KEY", "sk-live-1")

    proc = _GooseFakeProc(poll_results=(0,), stdout="x", stderr="")
    recorded = _patch_popen_goose(monkeypatch, proc)

    spec = _make_goose_spec(prompt="x", model_reference="qwen3-8b")
    execute_spec(spec, cfg=_EnvCfg())

    env = recorded["kwargs"]["env"]
    assert env is not None, "goose spec must pass env to Popen"
    assert env["GOOSE_PROVIDER"] == "openai"
    assert env["OPENAI_BASE_URL"] == "http://localhost:8080/v1"
    assert env["GOOSE_MODEL"] == "qwen3-8b"
    assert env["OPENAI_API_KEY"] == "sk-live-1"
    # Parent env is MERGED — PATH must remain (goose binary resolves via PATH)
    assert "PATH" in env  # inherited from os.environ


def test_goose_execute_threads_env_into_popen(monkeypatch):
    """execute() (harness='goose') threads the goose env into Popen."""
    proc = _GooseFakeProc(poll_results=(0,), stdout="x", stderr="")
    recorded = _patch_popen_goose(monkeypatch, proc)

    monkeypatch.setenv("MY_GOOSE_KEY2", "sk-live-2")

    class _EnvCfg(_GooseCfg):
        def get_goose_base_url(self):
            return "http://localhost:9000"

        def get_goose_api_key_env(self):
            return "MY_GOOSE_KEY2"

    execute(
        harness="goose",
        model_target="qwen3-8b",
        task="x",
        cwd="/tmp",
        cfg=_EnvCfg(),
    )

    env = recorded["kwargs"]["env"]
    assert env is not None
    assert env["GOOSE_PROVIDER"] == "openai"
    assert env["OPENAI_BASE_URL"] == "http://localhost:9000/v1"
    assert env["GOOSE_MODEL"] == "qwen3-8b"
    assert env["OPENAI_API_KEY"] == "sk-live-2"


def test_goose_spec_with_default_cfg_passes_env_none(monkeypatch):
    """When build_goose_env returns {} (default cfg + empty model), env=None."""
    proc = _GooseFakeProc(poll_results=(0,), stdout="x", stderr="")
    recorded = _patch_popen_goose(monkeypatch, proc)

    # Empty model_reference + empty cfg => build_goose_env returns {}.
    spec = _make_goose_spec(prompt="x", model_reference="")
    result = execute_spec(spec, cfg=_GooseCfg())
    # env=None (no base_url, no api_key_env, no model_target)
    assert "env" not in recorded["kwargs"], (
        "goose spec with empty model + empty cfg must pass env=None (inherit)"
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
    proc = _GooseFakeProc(poll_results=(0,), stdout="x", stderr="")
    recorded = _patch_popen_goose(monkeypatch, proc)

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
    proc = _GooseFakeProc(poll_results=(0,), stdout="x", stderr="")
    recorded = _patch_popen_goose(monkeypatch, proc)

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


def test_goose_argv_refus_when_binary_is_empty():
    """build_goose_argv raises a typed ValueError when cfg.get_goose_bin() is empty."""

    class _EmptyBinCfg(_GooseCfg):
        def get_goose_bin(self):
            return ""

    with pytest.raises(ValueError, match="goose"):
        build_goose_argv(model_target="m", task="t", cfg=_EmptyBinCfg())


def test_goose_argv_refus_when_binary_is_whitespace():
    """Whitespace-only goose_bin also refuses (shlex.split yields [])."""

    class _WhitespaceCfg(_GooseCfg):
        def get_goose_bin(self):
            return "   \t  "

    with pytest.raises(ValueError, match="goose"):
        build_goose_argv(model_target="m", task="t", cfg=_WhitespaceCfg())


def test_goose_spec_refus_missing_binary_no_popen(monkeypatch):
    """execute_spec refuses BEFORE any subprocess when goose_bin is empty."""

    class _EmptyBinCfg(_GooseCfg):
        def get_goose_bin(self):
            return ""

    called = _assert_no_popen_goose(monkeypatch, None)

    spec = _make_goose_spec(prompt="x", model_reference="m")
    with pytest.raises(ValueError, match="goose"):
        execute_spec(spec, cfg=_EmptyBinCfg())
    assert called["yes"] is False, (
        "Popen must NOT be called when the typed missing-binary refusal fires"
    )
