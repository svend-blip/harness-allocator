"""Tests for the ``crush`` adapter (Run 028 / HA-5 -- D3 + D4).

Contract (GOAL.md Run 028 Sec 1 D1/D2 and the binding constraints):

- ``build_crush_argv`` returns ``[<crush_bin>, run, --yolo, --quiet,
  --model, <model>?, <task>?]``: the bound argv is grounded in the
  installed crush v0.91.0 build (``crush run --help``). ``run`` is the
  headless one-shot subcommand ("Run a single non-interactive prompt"),
  ``--yolo`` is the auto-accept binding (analogous to qwen's
  ``--approval-mode yolo`` and aider's ``--yes-always``), ``--quiet``
  hides the spinner (mirror goose's ``-q``), ``--model <model>`` is a
  CLI flag (mirror aider / sweagent, NOT qwen / goose), and ``<task>``
  is the final positional element when supplied.
- ``build_crush_invocation`` matches the shlex-joined argv (mirror
  qwen/goose/aider/sweagent).
- ``build_crush_env`` carries ``OPENAI_BASE_URL`` (forced /v1) and
  ``OPENAI_API_KEY`` (read from the NAMED env var). Empty config +
  empty model_target -> empty dict (the qwen / goose contract). The
  model is NOT in env (it's a CLI flag).
- ``get_capabilities("crush")`` has EXACTLY eight contract groups
  with ``sessions.mode == "oneshot"``, ``execution.headless == True``,
  ``automation.non_interactive == True``, and extensions with EXACTLY
  six keys: ``{skills: True, mcp: True, custom_tools: False,
  repo_task_agent: False, git_aware: False, patch_output: False}``.
  The three specialized keys are PRESENT-and-False (Universal per
  GOAL.md Sec 3b), never omitted.
- Config defaults: each ``get_crush_*`` returns its documented default
  when the env is cleared and no ini section exists.
- Missing-binary refusal: ``build_crush_argv`` raises a typed
  ``ValueError`` naming ``crush`` when ``cfg.get_crush_bin()`` is empty
  or whitespace (mirror ``build_qwen_argv`` / ``build_goose_argv`` /
  ``build_aider_argv``).

crush is a SUPPORTED chat-style harness (the seventh SUPPORTED_HARNESS,
NOT experimental -- no ``_require_experimental_enabled`` gate). The
"*refus*" paths for crush are therefore ONLY the missing/empty-bin
refusal (no experimental gate refusal).

Test names DO NOT contain the bare token ``escalat`` (a run-035
rehearsal lesson -- it collides with an existing parametrized id).
This file has no such surface.

The eight existing harnesses' behaviour is intentionally untouched --
see test_harness_allocator.py, test_qwen_adapter.py, test_goose_adapter.py,
test_capabilities.py, test_experimental_adapters.py for those.

Stdlib + pytest only (the package's standing constraint). The crush
CLI is a pinned system tool (v0.91.0) -- we do NOT execute it here.
The adapter builders are pure string/list/dict builders (no
subprocess, no filesystem existence check).
"""

import os
import shlex
import sys
from pathlib import Path

import pytest

# Import the package from the project root (sibling of tests/).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness_allocator.adapter import (  # noqa: E402
    build_crush_argv,
    build_crush_env,
    build_crush_invocation,
    build_launch_argv,
    build_launch_command,
    build_task_argv,
    build_task_invocation,
)
from harness_allocator.capabilities import get_capabilities  # noqa: E402


# --- Test doubles (mirror the package's _FakeCfg / _UnsetEnv style) ---


class _CrushCfg:
    """Fixed cfg so argv/env assertions are byte-stable.

    Mirrors ``tests/test_goose_adapter.py::_GooseCfg`` for the crush
    surface: bin = "crush", no base_url, no api_key_env.
    """

    def get_crush_bin(self):
        return "crush"

    def get_crush_base_url(self):
        return ""

    def get_crush_api_key_env(self):
        return ""


class _CrushCfgBaseUrl(_CrushCfg):
    """Cfg variant with a base URL (forces /v1 in env)."""

    def get_crush_base_url(self):
        return "http://localhost:8080"


class _CrushCfgApiKeyName(_CrushCfg):
    """Cfg variant that supplies a NAMED env var for the API key."""

    def get_crush_api_key_env(self):
        return "MY_CRUSH_KEY"


class _CrushCfgFull(_CrushCfg):
    """Cfg variant with base_url + api_key_env both configured."""

    def get_crush_base_url(self):
        return "http://localhost:8080"

    def get_crush_api_key_env(self):
        return "MY_CRUSH_KEY"


class _EmptyBinCfg(_CrushCfg):
    """Cfg variant with empty crush_bin (forces the missing-binary refusal)."""

    def get_crush_bin(self):
        return ""


class _WhitespaceBinCfg(_CrushCfg):
    """Cfg variant with whitespace-only crush_bin (shlex.split yields [])."""

    def get_crush_bin(self):
        return "   \t  "


class _UnsetEnv:
    """Context manager: clear CRUSH_* env vars and restore prior values."""

    def __init__(self):
        self._keys = (
            "CRUSH_BIN",
            "CRUSH_BASE_URL",
            "CRUSH_API_KEY_ENV",
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


# --- A. argv golden (EXACT-list equality -- order is bound) ---------


def test_crush_argv_golden_minimum():
    """Default cfg + model + task: [crush, run, --yolo, --quiet, --model, m, t]."""
    argv = build_crush_argv(model_target="qwen3-8b", task="do a thing", cfg=_CrushCfg())
    assert argv == [
        "crush", "run", "--yolo", "--quiet",
        "--model", "qwen3-8b", "do a thing",
    ]


def test_crush_argv_golden_launch_form():
    """No model + no task -> [crush, run, --yolo, --quiet] (launch form)."""
    argv = build_crush_argv(model_target=None, task=None, cfg=_CrushCfg())
    assert argv == ["crush", "run", "--yolo", "--quiet"]


def test_crush_argv_run_subcommand_is_first_after_bin():
    """``run`` is the headless one-shot subcommand and appears at argv[1]."""
    argv = build_crush_argv(model_target="qwen3-8b", task="do a thing", cfg=_CrushCfg())
    assert argv[1] == "run"


def test_crush_argv_yolo_is_always_present():
    """``--yolo`` (auto-accept binding) is ALWAYS present in argv."""
    argv = build_crush_argv(model_target=None, task=None, cfg=_CrushCfg())
    assert "--yolo" in argv
    argv_min = build_crush_argv(model_target="m", task="t", cfg=_CrushCfg())
    assert "--yolo" in argv_min


def test_crush_argv_quiet_is_always_present():
    """``--quiet`` (spinner-hiding binding) is ALWAYS present in argv."""
    argv = build_crush_argv(model_target=None, task=None, cfg=_CrushCfg())
    assert "--quiet" in argv
    argv_min = build_crush_argv(model_target="m", task="t", cfg=_CrushCfg())
    assert "--quiet" in argv_min


def test_crush_argv_model_pair_present_when_model_given():
    """``--model <model>`` is appended as an adjacency pair when model is non-empty."""
    argv = build_crush_argv(model_target="qwen3-8b", task="t", cfg=_CrushCfg())
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "qwen3-8b"


def test_crush_argv_model_pair_absent_when_no_model():
    """Empty model_target -> ``--model`` is absent entirely."""
    argv = build_crush_argv(model_target=None, task="t", cfg=_CrushCfg())
    assert "--model" not in argv


def test_crush_argv_task_is_final_positional_element():
    """``<task>`` is the final positional element when supplied."""
    argv = build_crush_argv(model_target="m", task="do a thing", cfg=_CrushCfg())
    assert argv[-1] == "do a thing"


def test_crush_argv_task_is_single_element_with_newlines():
    """The full task is ONE argv element with embedded newlines preserved."""
    task = "Reply with exactly: OK\nSecond line\nThird line\nFinal"
    argv = build_crush_argv(model_target="m", task=task, cfg=_CrushCfg())
    assert argv[-1] == task
    assert argv[-1].count("\n") == 3


def test_crush_argv_task_omitted_when_empty():
    """Empty task -> the launch form is returned (no trailing positional)."""
    argv = build_crush_argv(model_target="m", cfg=_CrushCfg())
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "m"
    assert len(argv) == 6


def test_crush_argv_does_not_emit_cwd_flag():
    """Popen's ``cwd`` keyword handles the working directory; ``--cwd`` is NOT emitted."""
    argv = build_crush_argv(model_target="m", task="t", cfg=_CrushCfg())
    assert "--cwd" not in argv
    assert "-c" not in argv


def test_crush_argv_bin_supports_full_launcher_path():
    """bin may be a multi-token launcher (shlex-split, like qwen/goose/sweagent/aider)."""

    class Cfg(_CrushCfg):
        def get_crush_bin(self):
            return "/opt/crush/bin/crush --extra"

    argv = build_crush_argv(model_target="m", task="t", cfg=Cfg())
    # The shlex-split bin becomes the leading argv tokens.
    assert argv[0] == "/opt/crush/bin/crush"
    assert argv[1] == "--extra"
    assert argv[2] == "run"
    # The non-interactive trio is present, AFTER the bin tokens.
    assert "--yolo" in argv
    assert "--quiet" in argv


def test_crush_invocation_matches_argv_joined():
    """The shell command, when split, reconstructs the exact argv list."""
    task = "line one\nline two\nSupervisor"
    cmd = build_crush_invocation(model_target="qwen3-8b", task=task, cfg=_CrushCfg())
    argv = build_crush_argv(model_target="qwen3-8b", task=task, cfg=_CrushCfg())
    assert shlex.split(cmd) == argv


def test_crush_invocation_launch_form():
    """No task -> the invocation is the launch form."""
    cmd = build_crush_invocation(model_target="qwen3-8b", cfg=_CrushCfg())
    argv = build_crush_argv(model_target="qwen3-8b", cfg=_CrushCfg())
    assert shlex.split(cmd) == argv


# --- B. env builder (build_crush_env) --------------------------------


def test_crush_env_empty_when_nothing_configured():
    """Empty config + empty model_target -> empty dict (caller inherits parent env)."""
    env = build_crush_env(model_target="", cfg=_CrushCfg())
    assert env == {}


def test_crush_env_empty_when_model_target_only():
    """model_target is accepted for call-shape parity but NOT used in env."""
    env = build_crush_env(model_target="qwen3-8b", cfg=_CrushCfg())
    # model is a CLI flag, not env.
    assert env == {}


def test_crush_env_openai_base_url_forced_to_v1():
    """Non-empty base_url WITHOUT /v1 suffix is forced to end in /v1."""
    env = build_crush_env(model_target="qwen3-8b", cfg=_CrushCfgBaseUrl())
    assert env["OPENAI_BASE_URL"] == "http://localhost:8080/v1"


def test_crush_env_openai_base_url_already_v1_unchanged():
    """base_url already ending in /v1 is unchanged (no double suffix)."""

    class Cfg(_CrushCfg):
        def get_crush_base_url(self):
            return "http://localhost:8080/v1"

    env = build_crush_env(model_target="qwen3-8b", cfg=Cfg())
    assert env["OPENAI_BASE_URL"] == "http://localhost:8080/v1"


def test_crush_env_openai_base_url_trailing_slash_then_v1():
    """Trailing-slash base_url is normalised before the /v1 suffix is appended."""

    class Cfg(_CrushCfg):
        def get_crush_base_url(self):
            return "http://localhost:8080/"

    env = build_crush_env(model_target="qwen3-8b", cfg=Cfg())
    assert env["OPENAI_BASE_URL"] == "http://localhost:8080/v1"


def test_crush_env_openai_base_url_omitted_when_empty():
    """Empty base_url -> OPENAI_BASE_URL key is absent."""
    env = build_crush_env(model_target="qwen3-8b", cfg=_CrushCfg())
    assert "OPENAI_BASE_URL" not in env


def test_crush_env_api_key_read_from_named_env_var(monkeypatch):
    """OPENAI_API_KEY is read from the env var NAMED by api_key_env (no secret in config)."""
    monkeypatch.setenv("MY_CRUSH_KEY", "sk-secret-123")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    env = build_crush_env(model_target="qwen3-8b", cfg=_CrushCfgApiKeyName())
    assert env["OPENAI_API_KEY"] == "sk-secret-123"


def test_crush_env_api_key_omitted_when_name_empty():
    """Empty api_key_env name -> OPENAI_API_KEY key is absent."""
    env = build_crush_env(model_target="qwen3-8b", cfg=_CrushCfg())
    assert "OPENAI_API_KEY" not in env


def test_crush_env_api_key_omitted_when_named_var_unset(monkeypatch):
    """Named env var present but unset -> OPENAI_API_KEY key is absent."""
    monkeypatch.delenv("MY_CRUSH_KEY", raising=False)
    env = build_crush_env(model_target="qwen3-8b", cfg=_CrushCfgApiKeyName())
    assert "OPENAI_API_KEY" not in env


def test_crush_env_api_key_omitted_when_named_var_empty(monkeypatch):
    """Named env var present but empty -> OPENAI_API_KEY key is absent."""
    monkeypatch.setenv("MY_CRUSH_KEY", "")
    env = build_crush_env(model_target="qwen3-8b", cfg=_CrushCfgApiKeyName())
    assert "OPENAI_API_KEY" not in env


def test_crush_env_model_target_is_never_used_in_env(monkeypatch):
    """model_target is accepted for call-shape parity but NEVER appears in env."""
    monkeypatch.setenv("MY_CRUSH_KEY", "sk-x")
    env = build_crush_env(model_target="some-fancy-model", cfg=_CrushCfgFull())
    assert "OPENAI_MODEL" not in env
    assert "CRUSH_MODEL" not in env
    assert "some-fancy-model" not in str(env)
    assert env == {
        "OPENAI_BASE_URL": "http://localhost:8080/v1",
        "OPENAI_API_KEY": "sk-x",
    }


def test_crush_env_full_payload(monkeypatch):
    """All wired keys together: base_url normalised + key from named var."""
    monkeypatch.setenv("MY_CRUSH_KEY", "sk-live")
    env = build_crush_env(model_target="qwen3-8b", cfg=_CrushCfgFull())
    assert env == {
        "OPENAI_BASE_URL": "http://localhost:8080/v1",
        "OPENAI_API_KEY": "sk-live",
    }


# --- C. manifest shape (get_capabilities("crush")) ------------------


def test_crush_manifest_has_exactly_eight_groups():
    """get_capabilities("crush") returns EXACTLY eight contract groups."""
    manifest = get_capabilities("crush")
    expected = {"execution", "workspace", "sessions", "extensions",
                "automation", "concurrency", "lifecycle", "models"}
    assert set(manifest.keys()) == expected


def test_crush_manifest_extensions_has_exactly_six_keys():
    """extensions has EXACTLY six keys -- the universal set (Universal per GOAL.md Sec 3b)."""
    manifest = get_capabilities("crush")
    assert set(manifest["extensions"].keys()) == {
        "skills", "mcp", "custom_tools",
        "repo_task_agent", "git_aware", "patch_output",
    }


def test_crush_manifest_extensions_sorted_keys_are_bound():
    """extensions sorted keys list is exactly the six fields (sorted) -- sanity check."""
    manifest = get_capabilities("crush")
    assert sorted(manifest["extensions"].keys()) == [
        "custom_tools", "git_aware", "mcp",
        "patch_output", "repo_task_agent", "skills",
    ]


def test_crush_manifest_skills_is_true():
    """extensions.skills == True (README "Agent Skills" -- open standard)."""
    manifest = get_capabilities("crush")
    assert manifest["extensions"]["skills"] is True


def test_crush_manifest_mcp_is_true():
    """extensions.mcp == True (README "MCPs" -- http/stdio/sse)."""
    manifest = get_capabilities("crush")
    assert manifest["extensions"]["mcp"] is True


def test_crush_manifest_custom_tools_is_false():
    """extensions.custom_tools == False (crush's custom-tool surface IS MCP)."""
    manifest = get_capabilities("crush")
    assert manifest["extensions"]["custom_tools"] is False


def test_crush_manifest_three_specialized_keys_are_false():
    """The three specialized keys (repo_task_agent / git_aware / patch_output)
    are all False for crush -- PRESENT-and-False, never omitted (GOAL.md Sec 3b)."""
    manifest = get_capabilities("crush")
    assert manifest["extensions"]["repo_task_agent"] is False
    assert manifest["extensions"]["git_aware"] is False
    assert manifest["extensions"]["patch_output"] is False


def test_crush_manifest_sessions_mode_is_oneshot():
    """sessions.mode == 'oneshot' is BOUND (the adapter launches crush run)."""
    manifest = get_capabilities("crush")
    assert manifest["sessions"]["mode"] == "oneshot"


def test_crush_manifest_sessions_persistent_session_is_true():
    """sessions.persistent_session == True (README Session-Based feature bullet)."""
    manifest = get_capabilities("crush")
    assert manifest["sessions"]["persistent_session"] is True


def test_crush_manifest_sessions_session_resume_is_true():
    """sessions.session_resume == True (--session {id} / --continue / crush session)."""
    manifest = get_capabilities("crush")
    assert manifest["sessions"]["session_resume"] is True


def test_crush_manifest_execution_headless_is_true():
    """execution.headless == True (crush run is the headless one-shot)."""
    manifest = get_capabilities("crush")
    assert manifest["execution"]["headless"] is True
    assert manifest["execution"]["terminal"] is False
    assert manifest["execution"]["interactive"] is False


def test_crush_manifest_workspace_write_is_true():
    """workspace.workspace_write == True (crush edits the user's project in cwd)."""
    manifest = get_capabilities("crush")
    assert manifest["workspace"]["read_only"] is False
    assert manifest["workspace"]["workspace_write"] is True
    assert manifest["workspace"]["full_access"] is False


def test_crush_manifest_automation_all_non_interactive_flags_true():
    """automation.non_interactive / deterministic_exit / interrupt_safe all True."""
    manifest = get_capabilities("crush")
    assert manifest["automation"]["non_interactive"] is True
    assert manifest["automation"]["deterministic_exit"] is True
    assert manifest["automation"]["interrupt_safe"] is True


def test_crush_manifest_returns_fresh_copy():
    """Mutating the returned manifest must not affect the next call."""
    m1 = get_capabilities("crush")
    m1["execution"]["headless"] = False
    m2 = get_capabilities("crush")
    assert m2["execution"]["headless"] is True


# --- D. *refus* paths (missing/empty bin -- the bound refusal surface) ---


def test_crush_argv_refus_when_binary_is_empty():
    """build_crush_argv raises a typed ValueError naming crush when bin is empty."""
    with pytest.raises(ValueError, match="crush"):
        build_crush_argv(model_target="m", task="t", cfg=_EmptyBinCfg())


def test_crush_argv_refus_when_binary_is_whitespace():
    """Whitespace-only crush_bin also refuses (shlex.split yields [])."""
    with pytest.raises(ValueError, match="crush"):
        build_crush_argv(model_target="m", task="t", cfg=_WhitespaceBinCfg())


def test_crush_argv_refus_empty_bin_message_names_bin_key():
    """The error message names both crush and the empty-bin config key."""
    with pytest.raises(ValueError) as exc_info:
        build_crush_argv(model_target="m", task="t", cfg=_EmptyBinCfg())
    msg = str(exc_info.value)
    assert "crush" in msg
    assert "CRUSH_BIN" in msg or "[crush] bin" in msg


# --- E. routing (the four hub functions route crush) ----------------


def test_build_launch_argv_routes_crush():
    """build_launch_argv('crush', ...) -> build_crush_argv(...) shape."""
    argv = build_launch_argv("crush", model_target="qwen3-8b", task="x", cfg=_CrushCfg())
    assert argv == [
        "crush", "run", "--yolo", "--quiet",
        "--model", "qwen3-8b", "x",
    ]


def test_build_launch_command_routes_crush():
    """build_launch_command('crush', ...) returns the shell-string form."""
    cmd = build_launch_command("crush", model_target="qwen3-8b", task="x", cfg=_CrushCfg())
    argv = build_launch_argv("crush", model_target="qwen3-8b", task="x", cfg=_CrushCfg())
    assert shlex.split(cmd) == argv


def test_build_task_argv_routes_crush():
    """build_task_argv('crush', ...) is the one-shot shape (with task)."""
    argv = build_task_argv("crush", model_target="qwen3-8b", task="hello", cfg=_CrushCfg())
    assert argv == [
        "crush", "run", "--yolo", "--quiet",
        "--model", "qwen3-8b", "hello",
    ]


def test_build_task_invocation_routes_crush():
    """build_task_invocation('crush', ...) is the shell-string form."""
    cmd = build_task_invocation("crush", model_target="qwen3-8b", task="hello", cfg=_CrushCfg())
    argv = build_task_argv("crush", model_target="qwen3-8b", task="hello", cfg=_CrushCfg())
    assert shlex.split(cmd) == argv


def test_build_launch_argv_unknown_harness_still_raises():
    """Routing: an unknown harness key still raises ValueError (not crash)."""
    with pytest.raises(ValueError):
        build_launch_argv("bogus", model_target="m", task="t", cfg=_CrushCfg())


# --- F. config defaults (real config module, env cleared) -----------


def test_crush_bin_default_when_env_cleared_and_no_ini():
    """get_crush_bin() == 'crush' when CRUSH_BIN is unset and no ini section exists."""
    with _UnsetEnv():
        from harness_allocator import config
        assert config.get_crush_bin() == "crush"


def test_crush_base_url_default_is_empty():
    """get_crush_base_url() == '' when CRUSH_BASE_URL is unset (crush's own / crushrc-configured)."""
    with _UnsetEnv():
        from harness_allocator import config
        assert config.get_crush_base_url() == ""


def test_crush_api_key_env_default_is_empty():
    """get_crush_api_key_env() == '' when CRUSH_API_KEY_ENV is unset (a NAME, never the secret)."""
    with _UnsetEnv():
        from harness_allocator import config
        assert config.get_crush_api_key_env() == ""


def test_crush_bin_env_override_takes_precedence():
    """CRUSH_BIN env override wins over the default 'crush'."""
    with _UnsetEnv():
        os.environ["CRUSH_BIN"] = "/opt/crush/bin/crush"
        from harness_allocator import config
        assert config.get_crush_bin() == "/opt/crush/bin/crush"


def test_crush_base_url_env_override_takes_precedence():
    """CRUSH_BASE_URL env override wins over the empty default."""
    with _UnsetEnv():
        os.environ["CRUSH_BASE_URL"] = "http://example.test:9000"
        from harness_allocator import config
        assert config.get_crush_base_url() == "http://example.test:9000"


def test_crush_api_key_env_env_override_takes_precedence():
    """CRUSH_API_KEY_ENV env override wins over the empty default."""
    with _UnsetEnv():
        os.environ["CRUSH_API_KEY_ENV"] = "MY_CRUSH_KEY"
        from harness_allocator import config
        assert config.get_crush_api_key_env() == "MY_CRUSH_KEY"
