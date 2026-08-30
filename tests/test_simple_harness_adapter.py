"""Tests for the ``simple-harness`` adapter (Run 1010 / Objective A).

Contract (the spec at
/home/svend/flows/1010/SIMPLE-HARNESS-ADAPTER-SPEC.md and the binding
constraints):

- ``build_simple_harness_argv`` returns ``[<simple_harness_bin>, run,
  --base-url, <url>, --model, <model>, --workspace, <dir>?, --permission,
  read_only, --output, jsonl, --prompt-file, <file>, --max-turns, <n>?]``
  — the bound argv grounded in the installed ``simple-harness`` 0.1.0-dev
  build (``simple-harness run --help``, the contract docs at
  /home/svend/simple-harness/docs/HARNESS-CONTRACT.md and SCOPE.md).
  ``run`` is the headless one-shot subcommand; ``--base-url`` is required
  (SCOPE §28 — empty exits 2); ``--model`` is required (same); the
  adapter writes the task to a tempfile and threads that path via
  ``--prompt-file`` (the harness takes the prompt as a file path, not
  a string); ``--permission`` is always ``read_only`` (the safe default);
  ``--output`` is always ``jsonl`` (the spec mandates structured output).
- ``build_simple_harness_invocation`` matches the shlex-joined argv
  (mirror every other adapter).
- ``build_simple_harness_env`` carries ``SIMPLE_HARNESS_API_KEY`` (read
  from the NAMED env var) when wired; empty when nothing is wired (the
  qwen / goose / crush contract). The model is NOT in env (it's a CLI
  flag). The base URL is NOT in env (it's a CLI flag, opposite to
  qwen / goose / crush which use OPENAI_BASE_URL).
- ``get_capabilities("simple-harness")`` has EXACTLY eight contract
  groups with ``sessions.mode == "fresh"``, ``execution.headless == True``,
  ``automation.non_interactive == True``, and extensions with EXACTLY
  six keys: ``{skills: True, mcp: True, custom_tools: False,
  repo_task_agent: False, git_aware: False, patch_output: False}``.
  The three specialized keys are PRESENT-and-False (Universal per
  GOAL.md §3b), never omitted.
- Config defaults: each ``get_simple_harness_*`` returns its documented
  default when the env is cleared and no ini section exists.
- Missing-binary refusal: ``build_simple_harness_argv`` raises a typed
  ``ValueError`` naming ``simple-harness`` when ``cfg.get_simple_harness_bin()``
  is empty or whitespace (mirror ``build_qwen_argv`` / ``build_goose_argv``
  / ``build_aider_argv`` / ``build_crush_argv``).
- Missing-base-url refusal: ``build_simple_harness_argv`` raises a typed
  ``ValueError`` naming ``simple-harness`` when ``cfg.get_simple_harness_base_url()``
  is empty or whitespace (SCOPE §28: empty ``--base-url`` is exit 2; we
  surface it as a typed refusal BEFORE the subprocess).
- Missing-model refusal: ``build_simple_harness_argv`` raises a typed
  ``ValueError`` when ``model_target`` is empty / None (SCOPE §28:
  empty ``--model`` is exit 2).

simple-harness is a SUPPORTED chat-style harness (the ninth
SUPPORTED_HARNESS, NOT experimental — no ``_require_experimental_enabled``
gate). The "*refus*" paths are therefore ONLY the missing/empty-bin
+ missing/empty-base-url + missing-model refusals (no experimental gate
refusal).

Test names DO NOT contain the bare token ``escalat`` (a run-035 rehearsal
lesson — it collides with an existing parametrized id). This file has no
such surface.

Stdlib + pytest only (the package's standing constraint). The
simple-harness binary is a pinned system tool (0.1.0-dev) — we do NOT
execute it here. The adapter builders are pure string/list/dict builders
(no subprocess, no filesystem existence check). The ``build_simple_harness_argv``
does create a tempfile to thread the prompt via ``--prompt-file`` (the
harness's contract requires a file path, not a string); the tests
assert the path is in argv but do not pin the exact path.
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
    build_simple_harness_argv,
    build_simple_harness_env,
    build_simple_harness_invocation,
    build_task_argv,
    build_task_invocation,
)
from harness_allocator.capabilities import get_capabilities  # noqa: E402


# --- Test doubles (mirror the package's _FakeCfg / _UnsetEnv style) ---


class _SimpleHarnessCfg:
    """Fixed cfg so argv/env assertions are byte-stable.

    Mirrors ``tests/test_crush_adapter.py::_CrushCfg`` for the
    simple-harness surface: bin = "simple-harness", a non-empty base_url,
    no api_key_env, default max_turns=8, no workdir.
    """

    def get_simple_harness_bin(self):
        return "simple-harness"

    def get_simple_harness_base_url(self):
        return "http://localhost:11434"

    def get_simple_harness_api_key_env(self):
        return ""

    def get_simple_harness_max_turns(self):
        return 8

    def get_simple_harness_workdir(self):
        return ""


class _SimpleHarnessCfgFull(_SimpleHarnessCfg):
    """Cfg variant with all knobs configured (base_url with /v1, key, workdir)."""

    def get_simple_harness_api_key_env(self):
        return "MY_SIMPLE_HARNESS_KEY"

    def get_simple_harness_max_turns(self):
        return 3

    def get_simple_harness_workdir(self):
        return "/tmp/simple-harness-work"


class _EmptyBinCfg(_SimpleHarnessCfg):
    """Cfg variant with empty bin (forces the missing-binary refusal)."""

    def get_simple_harness_bin(self):
        return ""


class _WhitespaceBinCfg(_SimpleHarnessCfg):
    """Cfg variant with whitespace-only bin (shlex.split yields [])."""

    def get_simple_harness_bin(self):
        return "   \t  "


class _EmptyBaseUrlCfg(_SimpleHarnessCfg):
    """Cfg variant with empty base_url (forces the missing-base-url refusal)."""

    def get_simple_harness_base_url(self):
        return ""


class _WhitespaceBaseUrlCfg(_SimpleHarnessCfg):
    """Cfg variant with whitespace-only base_url."""

    def get_simple_harness_base_url(self):
        return "   \t  "


class _NoModelCfg(_SimpleHarnessCfg):
    """Cfg variant where model_target is forced to None (for missing-model refusal)."""

    pass  # model_target=None is the trigger


class _UnsetEnv:
    """Context manager: clear SIMPLE_HARNESS_* env vars and restore prior values."""

    def __init__(self):
        self._keys = (
            "SIMPLE_HARNESS_BIN",
            "SIMPLE_HARNESS_BASE_URL",
            "SIMPLE_HARNESS_API_KEY_ENV",
            "SIMPLE_HARNESS_MAX_TURNS",
            "SIMPLE_HARNESS_WORKDIR",
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


# --- A. argv golden (EXACT-list equality — order is bound) ---------


def test_simple_harness_argv_golden_minimum():
    """Default cfg + model + task: base argv with prompt-file path inserted.

    The exact tempfile path varies between runs (random suffix), so we
    pin every argv position except the prompt-file value (asserted via
    ``argv[argv.index('--prompt-file') + 1]``).
    """
    argv = build_simple_harness_argv(
        model_target="qwen3-coder-30b-32k:latest",
        task="do a thing",
        cfg=_SimpleHarnessCfg(),
    )
    # argv must start with [simple-harness, run, --base-url, URL, --model, MODEL,
    # --permission, read_only, --output, jsonl, --prompt-file, <file>, --max-turns, 8]
    assert argv[0] == "simple-harness"
    assert argv[1] == "run"
    assert argv[2] == "--base-url"
    assert argv[3] == "http://localhost:11434/v1"  # forced /v1
    assert argv[4] == "--model"
    assert argv[5] == "qwen3-coder-30b-32k:latest"
    assert "--permission" in argv
    assert argv[argv.index("--permission") + 1] == "read_only"
    assert "--output" in argv
    assert argv[argv.index("--output") + 1] == "jsonl"
    assert "--prompt-file" in argv
    # The prompt-file path must exist (the adapter writes task to it).
    prompt_file = argv[argv.index("--prompt-file") + 1]
    assert os.path.isfile(prompt_file)
    assert "--max-turns" in argv
    assert argv[argv.index("--max-turns") + 1] == "8"


def test_simple_harness_argv_run_subcommand_is_first_after_bin():
    """``run`` is the headless one-shot subcommand and appears at argv[1]."""
    argv = build_simple_harness_argv(
        model_target="qwen3-coder-30b-32k:latest",
        task="do a thing",
        cfg=_SimpleHarnessCfg(),
    )
    assert argv[1] == "run"


def test_simple_harness_argv_permission_is_always_read_only():
    """``--permission read_only`` is ALWAYS present (the safe-default ratchet)."""
    argv = build_simple_harness_argv(
        model_target="qwen3-coder-30b-32k:latest",
        task="t",
        cfg=_SimpleHarnessCfg(),
    )
    assert "--permission" in argv
    assert argv[argv.index("--permission") + 1] == "read_only"


def test_simple_harness_argv_output_is_always_jsonl():
    """``--output jsonl`` is ALWAYS present (the spec mandates structured events)."""
    argv = build_simple_harness_argv(
        model_target="qwen3-coder-30b-32k:latest",
        task="t",
        cfg=_SimpleHarnessCfg(),
    )
    assert "--output" in argv
    assert argv[argv.index("--output") + 1] == "jsonl"


def test_simple_harness_argv_max_turns_reflects_cfg():
    """``--max-turns`` reflects cfg.get_simple_harness_max_turns()."""
    argv = build_simple_harness_argv(
        model_target="m",
        task="t",
        cfg=_SimpleHarnessCfgFull(),  # max_turns=3
    )
    assert "--max-turns" in argv
    assert argv[argv.index("--max-turns") + 1] == "3"


def test_simple_harness_argv_max_turns_omitted_when_zero():
    """``--max-turns 0`` (cfg) -> the flag is omitted (harness defaults to 8)."""
    class Cfg(_SimpleHarnessCfg):
        def get_simple_harness_max_turns(self):
            return 0

    argv = build_simple_harness_argv(
        model_target="m",
        task="t",
        cfg=Cfg(),
    )
    assert "--max-turns" not in argv


def test_simple_harness_argv_workspace_omitted_when_empty():
    """Empty workdir -> ``--workspace`` is absent (harness defaults to cwd)."""
    argv = build_simple_harness_argv(
        model_target="m",
        task="t",
        cfg=_SimpleHarnessCfg(),  # workdir=""
    )
    assert "--workspace" not in argv


def test_simple_harness_argv_workspace_present_when_configured():
    """Non-empty workdir -> ``--workspace <dir>`` is emitted."""
    argv = build_simple_harness_argv(
        model_target="m",
        task="t",
        cfg=_SimpleHarnessCfgFull(),  # workdir="/tmp/simple-harness-work"
    )
    assert "--workspace" in argv
    assert argv[argv.index("--workspace") + 1] == "/tmp/simple-harness-work"


def test_simple_harness_argv_prompt_file_contains_task_text():
    """The tempfile written by the adapter contains the full task text."""
    task = "Reply with exactly: OK\nSecond line\nFinal"
    argv = build_simple_harness_argv(
        model_target="m",
        task=task,
        cfg=_SimpleHarnessCfg(),
    )
    prompt_file = argv[argv.index("--prompt-file") + 1]
    with open(prompt_file, "r") as f:
        content = f.read()
    assert content == task


def test_simple_harness_argv_supports_full_launcher_path():
    """bin may be a multi-token launcher (shlex-split, like qwen/goose/sweagent/aider)."""

    class Cfg(_SimpleHarnessCfg):
        def get_simple_harness_bin(self):
            return "/opt/simple-harness/bin/simple-harness --extra"

    argv = build_simple_harness_argv(
        model_target="m",
        task="t",
        cfg=Cfg(),
    )
    # The shlex-split bin becomes the leading argv tokens.
    assert argv[0] == "/opt/simple-harness/bin/simple-harness"
    assert argv[1] == "--extra"
    assert argv[2] == "run"
    # The non-interactive trio is present, AFTER the bin tokens.
    assert "--permission" in argv
    assert argv[argv.index("--permission") + 1] == "read_only"
    assert "--output" in argv
    assert argv[argv.index("--output") + 1] == "jsonl"


def test_simple_harness_argv_base_url_forced_to_v1():
    """Non-empty base_url WITHOUT /v1 suffix is forced to end in /v1."""
    argv = build_simple_harness_argv(
        model_target="m",
        task="t",
        cfg=_SimpleHarnessCfg(),  # base_url="http://localhost:11434"
    )
    assert argv[argv.index("--base-url") + 1] == "http://localhost:11434/v1"


def test_simple_harness_argv_base_url_already_v1_unchanged():
    """base_url already ending in /v1 is unchanged (no double suffix)."""

    class Cfg(_SimpleHarnessCfg):
        def get_simple_harness_base_url(self):
            return "http://localhost:11434/v1"

    argv = build_simple_harness_argv(
        model_target="m",
        task="t",
        cfg=Cfg(),
    )
    assert argv[argv.index("--base-url") + 1] == "http://localhost:11434/v1"


def test_simple_harness_argv_base_url_trailing_slash_then_v1():
    """Trailing-slash base_url is normalised before the /v1 suffix is appended."""

    class Cfg(_SimpleHarnessCfg):
        def get_simple_harness_base_url(self):
            return "http://localhost:11434/"

    argv = build_simple_harness_argv(
        model_target="m",
        task="t",
        cfg=Cfg(),
    )
    assert argv[argv.index("--base-url") + 1] == "http://localhost:11434/v1"


def test_simple_harness_invocation_matches_argv_joined():
    """The shell command, when split, has the expected argv SHAPE.

    Note: simple-harness writes task to a tempfile per call, so each
    invocation/argv call generates a fresh tempfile path. We assert the
    SHAPE (positions of fixed tokens, --prompt-file presence, file
    existence) rather than exact equality between two separate calls
    (which would create two distinct tempfiles). shlex.split of the
    invocation string is the canonical argv-list view.
    """
    task = "line one\nline two\nSupervisor"
    cmd = build_simple_harness_invocation(
        model_target="qwen3-coder-30b-32k:latest",
        task=task,
        cfg=_SimpleHarnessCfg(),
    )
    argv = shlex.split(cmd)
    # Shape check: every fixed token in the expected position.
    assert argv[0] == "simple-harness"
    assert argv[1] == "run"
    assert argv[argv.index("--base-url") + 1] == "http://localhost:11434/v1"
    assert argv[argv.index("--model") + 1] == "qwen3-coder-30b-32k:latest"
    assert argv[argv.index("--permission") + 1] == "read_only"
    assert argv[argv.index("--output") + 1] == "jsonl"
    assert "--prompt-file" in argv
    # The tempfile path exists and contains the task verbatim.
    prompt_file = argv[argv.index("--prompt-file") + 1]
    with open(prompt_file, "r") as f:
        assert f.read() == task


# --- B. env builder (build_simple_harness_env) --------------------------------


def test_simple_harness_env_empty_when_nothing_configured():
    """Empty config + empty model_target -> empty dict (caller inherits parent env)."""
    env = build_simple_harness_env(model_target="", cfg=_SimpleHarnessCfg())
    assert env == {}


def test_simple_harness_env_empty_when_model_target_only():
    """model_target is accepted for call-shape parity but NOT used in env.

    Unlike qwen / goose, simple-harness takes the model as a CLI flag
    (``--model <model>`` set by build_simple_harness_argv), NOT env.
    """
    env = build_simple_harness_env(
        model_target="qwen3-coder-30b-32k:latest", cfg=_SimpleHarnessCfg()
    )
    assert env == {}


def test_simple_harness_env_api_key_read_from_named_env_var(monkeypatch):
    """SIMPLE_HARNESS_API_KEY is read from the env var NAMED by api_key_env (no secret in config).

    **Honest boundary:** unlike qwen / goose / crush which write
    ``OPENAI_API_KEY``, simple-harness reads ``SIMPLE_HARNESS_API_KEY``
    (the harness's own namespacing per
    /home/svend/simple-harness/internal/config/config.go:320).
    """
    monkeypatch.setenv("MY_SIMPLE_HARNESS_KEY", "sk-secret-123")
    monkeypatch.delenv("SIMPLE_HARNESS_API_KEY", raising=False)

    env = build_simple_harness_env(
        model_target="qwen3-coder-30b-32k:latest", cfg=_SimpleHarnessCfgFull()
    )
    assert env["SIMPLE_HARNESS_API_KEY"] == "sk-secret-123"


def test_simple_harness_env_api_key_omitted_when_name_empty():
    """Empty api_key_env name -> SIMPLE_HARNESS_API_KEY key is absent."""
    env = build_simple_harness_env(
        model_target="qwen3-coder-30b-32k:latest", cfg=_SimpleHarnessCfg()
    )
    assert "SIMPLE_HARNESS_API_KEY" not in env


def test_simple_harness_env_api_key_omitted_when_named_var_unset(monkeypatch):
    """Named env var present but unset -> SIMPLE_HARNESS_API_KEY key is absent."""
    monkeypatch.delenv("MY_SIMPLE_HARNESS_KEY", raising=False)
    env = build_simple_harness_env(
        model_target="qwen3-coder-30b-32k:latest", cfg=_SimpleHarnessCfgFull()
    )
    assert "SIMPLE_HARNESS_API_KEY" not in env


def test_simple_harness_env_api_key_omitted_when_named_var_empty(monkeypatch):
    """Named env var present but empty -> SIMPLE_HARNESS_API_KEY key is absent."""
    monkeypatch.setenv("MY_SIMPLE_HARNESS_KEY", "")
    env = build_simple_harness_env(
        model_target="qwen3-coder-30b-32k:latest", cfg=_SimpleHarnessCfgFull()
    )
    assert "SIMPLE_HARNESS_API_KEY" not in env


def test_simple_harness_env_does_not_thread_openai_api_key(monkeypatch):
    """The env builder writes SIMPLE_HARNESS_API_KEY, NOT OPENAI_API_KEY.

    The harness reads its own SIMPLE_HARNESS_API_KEY env var (per
    config.go:320). Mirroring the qwen / crush OPENAI_API_KEY wiring
    would NOT help the harness find the key (it would just be ignored).
    """
    monkeypatch.setenv("MY_SIMPLE_HARNESS_KEY", "sk-x")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-y")
    env = build_simple_harness_env(
        model_target="qwen3-coder-30b-32k:latest", cfg=_SimpleHarnessCfgFull()
    )
    assert env.get("SIMPLE_HARNESS_API_KEY") == "sk-x"
    assert "OPENAI_API_KEY" not in env


def test_simple_harness_env_does_not_thread_openai_base_url(monkeypatch):
    """The env builder does NOT thread OPENAI_BASE_URL.

    The base URL is argv-only (the harness reads ``--base-url`` flag
    first, then ``SIMPLE_HARNESS_BASE_URL`` env — both flow through
    the harness's own precedence chain; re-threading OPENAI_BASE_URL
    here would be redundant and never used by the harness).
    """
    monkeypatch.setenv("OPENAI_BASE_URL", "http://wrong/v1")
    env = build_simple_harness_env(
        model_target="qwen3-coder-30b-32k:latest", cfg=_SimpleHarnessCfg()
    )
    assert "OPENAI_BASE_URL" not in env


# --- C. manifest shape (get_capabilities("simple-harness")) ------------------


def test_simple_harness_manifest_has_exactly_eight_groups():
    """get_capabilities("simple-harness") returns EXACTLY eight contract groups."""
    manifest = get_capabilities("simple-harness")
    expected = {
        "execution", "workspace", "sessions", "extensions",
        "automation", "concurrency", "lifecycle", "models",
    }
    assert set(manifest.keys()) == expected


def test_simple_harness_manifest_extensions_has_exactly_six_keys():
    """extensions has EXACTLY six keys -- the universal set (Universal per GOAL.md Sec 3b)."""
    manifest = get_capabilities("simple-harness")
    assert set(manifest["extensions"].keys()) == {
        "skills", "mcp", "custom_tools",
        "repo_task_agent", "git_aware", "patch_output",
    }


def test_simple_harness_manifest_skills_is_true():
    """extensions.skills == True (SCOPE §16 — human-invoked skills)."""
    manifest = get_capabilities("simple-harness")
    assert manifest["extensions"]["skills"] is True


def test_simple_harness_manifest_mcp_is_true():
    """extensions.mcp == True (V1.x §43 — config-pinned MCP servers)."""
    manifest = get_capabilities("simple-harness")
    assert manifest["extensions"]["mcp"] is True


def test_simple_harness_manifest_custom_tools_is_false():
    """extensions.custom_tools == False (no custom-tool plugin surface per the spec)."""
    manifest = get_capabilities("simple-harness")
    assert manifest["extensions"]["custom_tools"] is False


def test_simple_harness_manifest_three_specialized_keys_are_false():
    """The three specialized keys (repo_task_agent / git_aware / patch_output)
    are all False for simple-harness -- PRESENT-and-False, never omitted
    (GOAL.md Sec 3b)."""
    manifest = get_capabilities("simple-harness")
    assert manifest["extensions"]["repo_task_agent"] is False
    assert manifest["extensions"]["git_aware"] is False
    assert manifest["extensions"]["patch_output"] is False


def test_simple_harness_manifest_sessions_mode_is_fresh():
    """sessions.mode == 'fresh' (BOUND — reuses the existing 'fresh' value bound to codex)."""
    manifest = get_capabilities("simple-harness")
    assert manifest["sessions"]["mode"] == "fresh"


def test_simple_harness_manifest_sessions_persistent_session_is_false():
    """sessions.persistent_session == False (sessions persist on DISK but resume is DEFERRED in V1)."""
    manifest = get_capabilities("simple-harness")
    assert manifest["sessions"]["persistent_session"] is False


def test_simple_harness_manifest_sessions_session_resume_is_false():
    """sessions.session_resume == False (V1 has no --resume)."""
    manifest = get_capabilities("simple-harness")
    assert manifest["sessions"]["session_resume"] is False


def test_simple_harness_manifest_execution_headless_is_true():
    """execution.headless == True (``simple-harness run`` is the headless one-shot)."""
    manifest = get_capabilities("simple-harness")
    assert manifest["execution"]["headless"] is True


def test_simple_harness_manifest_execution_terminal_is_true():
    """execution.terminal == True (per the spec: terminal=True, headless=True, interactive=True)."""
    manifest = get_capabilities("simple-harness")
    assert manifest["execution"]["terminal"] is True


def test_simple_harness_manifest_execution_interactive_is_true():
    """execution.interactive == True (per the spec — interactive mode exists)."""
    manifest = get_capabilities("simple-harness")
    assert manifest["execution"]["interactive"] is True


def test_simple_harness_manifest_workspace_all_three_modes_true():
    """workspace.read_only / workspace_write / full_access are ALL True.

    Per the spec: "--permission read_only|workspace_write|full_access" — the
    harness supports all three permission modes; the adapter always
    emits ``--permission read_only`` for the safe default, but the
    manifest declares the full surface (PRESENT-and-True, never omitted).
    """
    manifest = get_capabilities("simple-harness")
    assert manifest["workspace"]["read_only"] is True
    assert manifest["workspace"]["workspace_write"] is True
    assert manifest["workspace"]["full_access"] is True


def test_simple_harness_manifest_automation_all_non_interactive_flags_true():
    """automation.non_interactive / deterministic_exit / interrupt_safe all True."""
    manifest = get_capabilities("simple-harness")
    assert manifest["automation"]["non_interactive"] is True
    assert manifest["automation"]["deterministic_exit"] is True
    assert manifest["automation"]["interrupt_safe"] is True


def test_simple_harness_manifest_concurrency_exclusive_writer_is_true():
    """concurrency.exclusive_workspace_writer == True (per the spec — ADR-002 single writer)."""
    manifest = get_capabilities("simple-harness")
    assert manifest["concurrency"]["exclusive_workspace_writer"] is True
    assert manifest["concurrency"]["parallel_readers"] is True


def test_simple_harness_manifest_lifecycle_interrupts_and_cleans():
    """lifecycle.interrupt_current_task / child_process_cleanup == True; resumable_session == False."""
    manifest = get_capabilities("simple-harness")
    assert manifest["lifecycle"]["interrupt_current_task"] is True
    assert manifest["lifecycle"]["child_process_cleanup"] is True
    assert manifest["lifecycle"]["resumable_session"] is False


def test_simple_harness_models_openai_compatible_endpoint_is_true():
    """models.openai_compatible_endpoint == True (--base-url + SIMPLE_HARNESS_API_KEY env)."""
    manifest = get_capabilities("simple-harness")
    assert manifest["models"]["openai_compatible_endpoint"] is True


def test_simple_harness_manifest_returns_fresh_copy():
    """Mutating the returned manifest must not affect the next call."""
    m1 = get_capabilities("simple-harness")
    m1["execution"]["headless"] = False
    m2 = get_capabilities("simple-harness")
    assert m2["execution"]["headless"] is True


# --- D. *refus* paths (the bound refusal surface) ---


def test_simple_harness_argv_refus_when_binary_is_empty():
    """build_simple_harness_argv raises a typed ValueError naming simple-harness when bin is empty."""
    with pytest.raises(ValueError, match="simple-harness"):
        build_simple_harness_argv(
            model_target="m", task="t", cfg=_EmptyBinCfg()
        )


def test_simple_harness_argv_refus_when_binary_is_whitespace():
    """Whitespace-only simple_harness_bin also refuses (shlex.split yields [])."""
    with pytest.raises(ValueError, match="simple-harness"):
        build_simple_harness_argv(
            model_target="m", task="t", cfg=_WhitespaceBinCfg()
        )


def test_simple_harness_argv_refus_empty_bin_message_names_bin_key():
    """The error message names both simple-harness and the empty-bin config key."""
    with pytest.raises(ValueError) as exc_info:
        build_simple_harness_argv(
            model_target="m", task="t", cfg=_EmptyBinCfg()
        )
    msg = str(exc_info.value)
    assert "simple-harness" in msg
    assert "SIMPLE_HARNESS_BIN" in msg or "[simple-harness] bin" in msg


def test_simple_harness_argv_refus_when_base_url_is_empty():
    """build_simple_harness_argv raises a typed ValueError when base_url is empty (SCOPE §28)."""
    with pytest.raises(ValueError, match="simple-harness"):
        build_simple_harness_argv(
            model_target="m", task="t", cfg=_EmptyBaseUrlCfg()
        )


def test_simple_harness_argv_refus_when_base_url_is_whitespace():
    """Whitespace-only base_url also refuses."""
    with pytest.raises(ValueError, match="simple-harness"):
        build_simple_harness_argv(
            model_target="m", task="t", cfg=_WhitespaceBaseUrlCfg()
        )


def test_simple_harness_argv_refus_empty_base_url_message_names_base_url_key():
    """The error message names both simple-harness and the empty-base-url config key."""
    with pytest.raises(ValueError) as exc_info:
        build_simple_harness_argv(
            model_target="m", task="t", cfg=_EmptyBaseUrlCfg()
        )
    msg = str(exc_info.value)
    assert "simple-harness" in msg
    assert "SIMPLE_HARNESS_BASE_URL" in msg or "[simple-harness] base_url" in msg


def test_simple_harness_argv_refus_when_model_is_empty():
    """build_simple_harness_argv raises a typed ValueError when model_target is empty (SCOPE §28)."""
    with pytest.raises(ValueError, match="simple-harness"):
        build_simple_harness_argv(
            model_target="", task="t", cfg=_SimpleHarnessCfg()
        )


def test_simple_harness_argv_refus_when_model_is_none():
    """build_simple_harness_argv raises a typed ValueError when model_target is None (SCOPE §28)."""
    with pytest.raises(ValueError, match="simple-harness"):
        build_simple_harness_argv(
            model_target=None, task="t", cfg=_SimpleHarnessCfg()
        )


# --- E. routing (the four hub functions route simple-harness) ----------------


def test_build_launch_argv_routes_simple_harness():
    """build_launch_argv('simple-harness', ...) -> build_simple_harness_argv(...) shape."""
    argv = build_launch_argv(
        "simple-harness",
        model_target="qwen3-coder-30b-32k:latest",
        task="x",
        cfg=_SimpleHarnessCfg(),
    )
    assert argv[0] == "simple-harness"
    assert argv[1] == "run"
    assert argv[argv.index("--model") + 1] == "qwen3-coder-30b-32k:latest"
    assert "--permission" in argv
    assert "--output" in argv


def test_build_launch_command_routes_simple_harness():
    """build_launch_command('simple-harness', ...) returns the shell-string form.

    Shape-check rather than exact argv equality — simple-harness writes
    task to a tempfile per call (different tempfile paths across calls).
    """
    cmd = build_launch_command(
        "simple-harness",
        model_target="qwen3-coder-30b-32k:latest",
        task="x",
        cfg=_SimpleHarnessCfg(),
    )
    argv = shlex.split(cmd)
    assert argv[0] == "simple-harness"
    assert argv[1] == "run"
    assert argv[argv.index("--model") + 1] == "qwen3-coder-30b-32k:latest"
    assert argv[argv.index("--permission") + 1] == "read_only"
    assert argv[argv.index("--output") + 1] == "jsonl"
    assert "--prompt-file" in argv


def test_build_task_argv_routes_simple_harness():
    """build_task_argv('simple-harness', ...) is the one-shot shape (with task)."""
    argv = build_task_argv(
        "simple-harness",
        model_target="qwen3-coder-30b-32k:latest",
        task="hello",
        cfg=_SimpleHarnessCfg(),
    )
    assert argv[0] == "simple-harness"
    assert argv[1] == "run"
    assert "--prompt-file" in argv
    # Task is written to a tempfile, so it lives in argv by-reference.
    prompt_file = argv[argv.index("--prompt-file") + 1]
    with open(prompt_file, "r") as f:
        assert f.read() == "hello"


def test_build_task_invocation_routes_simple_harness():
    """build_task_invocation('simple-harness', ...) is the shell-string form.

    Shape-check rather than exact argv equality — simple-harness writes
    task to a tempfile per call (different tempfile paths across calls).
    """
    cmd = build_task_invocation(
        "simple-harness",
        model_target="qwen3-coder-30b-32k:latest",
        task="hello",
        cfg=_SimpleHarnessCfg(),
    )
    argv = shlex.split(cmd)
    assert argv[0] == "simple-harness"
    assert argv[1] == "run"
    assert argv[argv.index("--model") + 1] == "qwen3-coder-30b-32k:latest"
    assert "--prompt-file" in argv
    # Verify the prompt file contains the task verbatim.
    prompt_file = argv[argv.index("--prompt-file") + 1]
    with open(prompt_file, "r") as f:
        assert f.read() == "hello"


def test_build_launch_argv_unknown_harness_still_raises():
    """Routing: an unknown harness key still raises ValueError (not crash)."""
    with pytest.raises(ValueError):
        build_launch_argv("bogus", model_target="m", task="t", cfg=_SimpleHarnessCfg())


# --- F. config defaults (real config module, env cleared) -----------


def test_simple_harness_bin_default_when_env_cleared_and_no_ini():
    """get_simple_harness_bin() == 'simple-harness' when SIMPLE_HARNESS_BIN is unset and no ini section exists."""
    with _UnsetEnv():
        from harness_allocator import config
        assert config.get_simple_harness_bin() == "simple-harness"


def test_simple_harness_base_url_default_is_empty():
    """get_simple_harness_base_url() == '' when SIMPLE_HARNESS_BASE_URL is unset (REQUIRED by the harness, adapter refuses)."""
    with _UnsetEnv():
        from harness_allocator import config
        assert config.get_simple_harness_base_url() == ""


def test_simple_harness_api_key_env_default_is_empty():
    """get_simple_harness_api_key_env() == '' when SIMPLE_HARNESS_API_KEY_ENV is unset (a NAME, never the secret)."""
    with _UnsetEnv():
        from harness_allocator import config
        assert config.get_simple_harness_api_key_env() == ""


def test_simple_harness_max_turns_default_is_8():
    """get_simple_harness_max_turns() == 8 when SIMPLE_HARNESS_MAX_TURNS is unset (the harness's own default)."""
    with _UnsetEnv():
        from harness_allocator import config
        assert config.get_simple_harness_max_turns() == 8


def test_simple_harness_workdir_default_is_empty():
    """get_simple_harness_workdir() == '' when SIMPLE_HARNESS_WORKDIR is unset (harness defaults to cwd)."""
    with _UnsetEnv():
        from harness_allocator import config
        assert config.get_simple_harness_workdir() == ""


def test_simple_harness_bin_env_override_takes_precedence():
    """SIMPLE_HARNESS_BIN env override wins over the default 'simple-harness'."""
    with _UnsetEnv():
        os.environ["SIMPLE_HARNESS_BIN"] = "/opt/simple-harness/bin/simple-harness"
        from harness_allocator import config
        assert config.get_simple_harness_bin() == "/opt/simple-harness/bin/simple-harness"


def test_simple_harness_base_url_env_override_takes_precedence():
    """SIMPLE_HARNESS_BASE_URL env override wins over the empty default."""
    with _UnsetEnv():
        os.environ["SIMPLE_HARNESS_BASE_URL"] = "http://example.test:9000"
        from harness_allocator import config
        assert config.get_simple_harness_base_url() == "http://example.test:9000"


def test_simple_harness_api_key_env_env_override_takes_precedence():
    """SIMPLE_HARNESS_API_KEY_ENV env override wins over the empty default."""
    with _UnsetEnv():
        os.environ["SIMPLE_HARNESS_API_KEY_ENV"] = "MY_SIMPLE_HARNESS_KEY"
        from harness_allocator import config
        assert config.get_simple_harness_api_key_env() == "MY_SIMPLE_HARNESS_KEY"


def test_simple_harness_max_turns_env_override_takes_precedence():
    """SIMPLE_HARNESS_MAX_TURNS env override wins over the default 8."""
    with _UnsetEnv():
        os.environ["SIMPLE_HARNESS_MAX_TURNS"] = "3"
        from harness_allocator import config
        assert config.get_simple_harness_max_turns() == 3


def test_simple_harness_workdir_env_override_takes_precedence():
    """SIMPLE_HARNESS_WORKDIR env override wins over the empty default."""
    with _UnsetEnv():
        os.environ["SIMPLE_HARNESS_WORKDIR"] = "/opt/work"
        from harness_allocator import config
        assert config.get_simple_harness_workdir() == "/opt/work"
