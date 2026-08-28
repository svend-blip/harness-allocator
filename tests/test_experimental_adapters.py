"""Tests for the ``sweagent`` and ``aider`` adapters (Run 027 / HA-4 — D4 hermetic tests).

Contract (GOAL.md Run 027 §1 D4):

- ``build_sweagent_argv`` returns ``[<sweagent_bin>, run,
  --agent.model.name <model>?, --env.repo.path <path>?,
  --problem_statement.text <task>?]``: the model travels as a CLI flag
  (like aider, NOT in the child env). The bound argv is grounded in
  the installed sweagent 1.1.0 build (``sweagent run --help``).
- ``build_sweagent_env`` carries ``SWE_AGENT_CONFIG_DIR``,
  ``SWE_AGENT_TOOLS_DIR``, ``SWE_AGENT_TRAJECTORY_DIR`` (always set —
  the bare CLI asserts on ``CONFIG_DIR.is_dir()``). Empty config ->
  home-relative default under ``~/tools/SWE-agent/...``.
- ``build_aider_argv`` returns ``[<aider_bin>, --yes-always,
  --no-auto-commits, --no-dirty-commits, --model <model>?, --message
  <task>?]``: the no-auto-commit trio is ALWAYS emitted in this order
  (the git-policy stays with the Human per Run 027 / HA-4 §2).
- ``build_aider_env`` ALWAYS returns ``{}`` (aider reads
  ``OPENAI_API_KEY`` itself; the invoke layer falls through to
  ``env=None``).
- ``get_capabilities("sweagent")`` has EXACTLY eight contract groups
  with extensions ``{..., repo_task_agent: True}`` (the SPECIALIZED
  repo-task capability, ROADMAP §3).
- ``get_capabilities("aider")`` has EXACTLY eight contract groups
  with extensions ``{..., git_aware: True, patch_output: True}`` (the
  SPECIALIZED git/patch capabilities, ROADMAP §3).
- The D3 experimental gate: ``build_sweagent_argv`` /
  ``build_aider_argv`` refuse with a typed ``ValueError`` naming the
  harness and the ``[experimental] enabled_harnesses`` opt-in clause
  when the harness is NOT in
  ``cfg.get_experimental_enabled_harnesses()``.

Stdlib + pytest only (the package's standing constraint). The sweagent
and aider CLIs are pinned system tools (1.1.0 / 0.86.2) — we do NOT
execute them here. No spec-routing tests (handoff 127 / D2 owns
``-k spec``); this handoff owns the D4 hermetic surface — the argv +
env + manifest + enumeration + gate shapes only.

Test names contain substrings ``refus`` and ``auto_commit`` so the
TG2/TG3 pytest ``-k`` selectors collect the gate-refusal and the
no-auto-commit-binding tests, respectively.

The four existing harnesses' behaviour is intentionally untouched —
see test_harness_allocator.py, test_qwen_adapter.py,
test_capabilities.py for those.
"""

import os
import sys
from pathlib import Path

import pytest

# Import the package from the project root (sibling of tests/).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness_allocator.adapter import (  # noqa: E402
    build_aider_argv,
    build_aider_env,
    build_sweagent_argv,
    build_sweagent_env,
)
from harness_allocator.capabilities import (  # noqa: E402
    EXPERIMENTAL_HARNESSES,
    SUPPORTED_HARNESSES,
    get_capabilities,
)


# ── Test doubles (mirror the package's _FakeCfg / _UnsetEnv style) ────


class _SweagentCfg:
    """Fixed cfg so sweagent argv/env assertions are byte-stable.

    Mirrors ``tests/test_goose_adapter.py::_GooseCfg`` for the sweagent
    surface: bin = "sweagent", no repo_path, no env-overridden dirs.
    """

    def get_sweagent_bin(self):
        return "sweagent"

    def get_sweagent_config_dir(self):
        return ""

    def get_sweagent_tools_dir(self):
        return ""

    def get_sweagent_trajectory_dir(self):
        return ""

    def get_sweagent_repo_path(self):
        return ""

    def get_experimental_enabled_harnesses(self):
        return {"sweagent"}


class _SweagentCfgDisabled(_SweagentCfg):
    """Cfg variant with the experimental gate CLOSED for sweagent."""

    def get_experimental_enabled_harnesses(self):
        return set()


class _SweagentCfgRepo(_SweagentCfg):
    """Cfg variant that supplies a non-empty repo_path (forces --env.repo.path)."""

    def get_sweagent_repo_path(self):
        return "/srv/repo"


class _AiderCfg:
    """Fixed cfg so aider argv assertions are byte-stable.

    Mirrors ``tests/test_goose_adapter.py::_GooseCfg`` for the aider
    surface: bin = "aider". No additional setters — aider has no
    load-bearing child env.
    """

    def get_aider_bin(self):
        return "aider"

    def get_experimental_enabled_harnesses(self):
        return {"aider"}


class _AiderCfgDisabled(_AiderCfg):
    """Cfg variant with the experimental gate CLOSED for aider."""

    def get_experimental_enabled_harnesses(self):
        return set()


class _UnsetEnv:
    """Context manager: clear SWEAGENT_* / AIDER_* / EXPERIMENTAL_* env vars and restore."""

    def __init__(self):
        self._keys = (
            "SWEAGENT_BIN",
            "SWEAGENT_CONFIG_DIR",
            "SWEAGENT_TOOLS_DIR",
            "SWEAGENT_TRAJECTORY_DIR",
            "SWEAGENT_REPO_PATH",
            "AIDER_BIN",
            "EXPERIMENTAL_ENABLED_HARNESSES",
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


# ── A. sweagent argv golden (EXACT-list equality — order is bound) ──────


def test_sweagent_argv_golden_minimum():
    """Default cfg + model + task: [sweagent, run, --agent.model.name, m, --problem_statement.text, t]."""
    argv = build_sweagent_argv(model_target="m", task="t", cfg=_SweagentCfg())
    assert argv == [
        "sweagent", "run",
        "--agent.model.name", "m",
        "--problem_statement.text", "t",
    ]


def test_sweagent_argv_golden_launch_form():
    """No model + no task -> [sweagent, run] (launch form)."""
    argv = build_sweagent_argv(model_target=None, task=None, cfg=_SweagentCfg())
    assert argv == ["sweagent", "run"]


def test_sweagent_argv_repo_path_pair_present_when_set():
    """A non-empty repo_path inserts ``--env.repo.path <path>`` in argv."""
    argv = build_sweagent_argv(
        model_target="m", task="t", cfg=_SweagentCfgRepo()
    )
    assert "--env.repo.path" in argv
    assert argv[argv.index("--env.repo.path") + 1] == "/srv/repo"


def test_sweagent_argv_repo_path_pair_absent_when_empty():
    """Empty repo_path -> ``--env.repo.path`` is absent from argv."""
    argv = build_sweagent_argv(model_target="m", task="t", cfg=_SweagentCfg())
    assert "--env.repo.path" not in argv


def test_sweagent_argv_run_subcommand_is_first_token():
    """``run`` is the headless one-shot subcommand and appears at argv[1]."""
    argv = build_sweagent_argv(model_target="m", task="t", cfg=_SweagentCfg())
    assert argv[1] == "run"


def test_sweagent_argv_bin_supports_full_launcher_path():
    """bin may be a multi-token launcher (shlex-split, like goose)."""

    class Cfg(_SweagentCfg):
        def get_sweagent_bin(self):
            return "/opt/sweagent/bin/sweagent --extra"

    argv = build_sweagent_argv(model_target="m", task="t", cfg=Cfg())
    # The shlex-split bin becomes the leading argv tokens.
    assert argv[0] == "/opt/sweagent/bin/sweagent"
    assert argv[1] == "--extra"
    assert argv[2] == "run"


def test_sweagent_argv_task_is_single_element_with_newlines():
    """The full task is one argv element with embedded newlines preserved."""
    task = "Reply with exactly: OK\nSecond line\nThird line\nFinal"
    argv = build_sweagent_argv(model_target="m", task=task, cfg=_SweagentCfg())
    assert argv[-2:] == ["--problem_statement.text", task]
    assert argv[-1] == task
    assert argv[-1].count("\n") == 3


def test_sweagent_argv_task_omitted_when_empty():
    """No task -> ``--problem_statement.text`` is absent entirely (launch form)."""
    argv = build_sweagent_argv(model_target="m", cfg=_SweagentCfg())
    assert "--problem_statement.text" not in argv


def test_sweagent_argv_model_omitted_when_empty():
    """No model_target -> ``--agent.model.name`` is absent entirely."""
    argv = build_sweagent_argv(model_target=None, task="t", cfg=_SweagentCfg())
    assert "--agent.model.name" not in argv


# ── B. aider argv golden (ELEMENT-PRESENCE + FLAG-ADJACENCY — NOT positional) ──


def test_aider_argv_golden_bin_and_trio():
    """[aider, --yes-always, --no-auto-commits, --no-dirty-commits] — trio first."""
    argv = build_aider_argv(model_target=None, task=None, cfg=_AiderCfg())
    assert argv[0] == "aider"
    assert "--yes-always" in argv
    assert "--no-auto-commits" in argv
    assert "--no-dirty-commits" in argv


def test_aider_argv_golden_model_pair_present():
    """``--model <model>`` is appended as an adjacency pair."""
    argv = build_aider_argv(model_target="m", cfg=_AiderCfg())
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "m"


def test_aider_argv_golden_message_pair_present():
    """``--message <task>`` is appended as an adjacency pair."""
    argv = build_aider_argv(task="t", cfg=_AiderCfg())
    assert "--message" in argv
    assert argv[argv.index("--message") + 1] == "t"


def test_aider_argv_model_pair_absent_when_no_model():
    """Empty model_target -> ``--model`` is absent entirely."""
    argv = build_aider_argv(model_target=None, task="t", cfg=_AiderCfg())
    assert "--model" not in argv


def test_aider_argv_message_pair_absent_when_no_task():
    """Empty task -> ``--message`` is absent entirely (launch form)."""
    argv = build_aider_argv(model_target="m", cfg=_AiderCfg())
    assert "--message" not in argv


def test_aider_argv_pins_no_auto_commit():
    """TG3 — argv pins BOTH ``--no-auto-commits`` AND ``--no-dirty-commits``.

    This is the bind-point for the no-auto-commit policy (Run 027 / HA-4
    §2 — the git-policy stays with the Human; the adapter refuses to
    emit aider's default auto-commit / dirty-commit behaviour).
    """
    argv = build_aider_argv(model_target="m", task="t", cfg=_AiderCfg())
    assert "--no-auto-commits" in argv
    assert "--no-dirty-commits" in argv


def test_aider_argv_bin_supports_full_launcher_path():
    """bin may be a multi-token launcher (shlex-split, like goose / sweagent)."""

    class Cfg(_AiderCfg):
        def get_aider_bin(self):
            return "/opt/aider/bin/aider --extra"

    argv = build_aider_argv(model_target="m", task="t", cfg=Cfg())
    # The shlex-split bin becomes the leading argv tokens.
    assert argv[0] == "/opt/aider/bin/aider"
    assert argv[1] == "--extra"
    # The non-interactive trio is present, AFTER the bin tokens.
    assert "--yes-always" in argv
    assert "--no-auto-commits" in argv
    assert "--no-dirty-commits" in argv


# ── C. D3 experimental gate (TG2) ──────────────────────────────────────


def test_experimental_gate_refuses_disabled_sweagent():
    """TG2 — sweagent refuses with a typed ValueError when gate is CLOSED.

    The error names ``sweagent`` AND the
    ``[experimental] enabled_harnesses`` opt-in clause (the D3 gate
    surfaces the user-fixable knob in the message).
    """
    with pytest.raises(ValueError) as excinfo:
        build_sweagent_argv(model_target="m", task="t", cfg=_SweagentCfgDisabled())
    msg = str(excinfo.value)
    assert "sweagent" in msg
    assert "[experimental] enabled_harnesses" in msg


def test_experimental_gate_refuses_disabled_aider():
    """TG2 — aider refuses with a typed ValueError when gate is CLOSED.

    The error names ``aider`` AND the
    ``[experimental] enabled_harnesses`` opt-in clause.
    """
    with pytest.raises(ValueError) as excinfo:
        build_aider_argv(model_target="m", task="t", cfg=_AiderCfgDisabled())
    msg = str(excinfo.value)
    assert "aider" in msg
    assert "[experimental] enabled_harnesses" in msg


def test_experimental_gate_allows_enabled_harnesses():
    """Positive half — both adapters build their argv without error when enabled."""
    argv_sw = build_sweagent_argv(model_target="m", task="t", cfg=_SweagentCfg())
    argv_ai = build_aider_argv(model_target="m", task="t", cfg=_AiderCfg())
    assert argv_sw[0] == "sweagent"
    assert argv_ai[0] == "aider"


def test_experimental_gate_refuses_default_cfg_no_env():
    """A vanilla cfg (no env, no ini, default gate set = empty) refuses both."""
    with _UnsetEnv():
        from harness_allocator import config as pkg_config

        with pytest.raises(ValueError, match="sweagent"):
            build_sweagent_argv(
                model_target="m", task="t", cfg=pkg_config
            )
        with pytest.raises(ValueError, match="aider"):
            build_aider_argv(
                model_target="m", task="t", cfg=pkg_config
            )


# ── D. manifest shape (extensions — repo_task_agent / git_aware / patch_output) ──


def test_sweagent_manifest_has_exactly_eight_groups():
    """get_capabilities('sweagent') returns EXACTLY eight contract groups."""
    manifest = get_capabilities("sweagent")
    expected = {"execution", "workspace", "sessions", "extensions",
                "automation", "concurrency", "lifecycle", "models"}
    assert set(manifest.keys()) == expected


def test_sweagent_manifest_extensions_repo_task_agent_is_true():
    """extensions.repo_task_agent == True (ROADMAP §3 — SPECIALIZED repo-task signal)."""
    manifest = get_capabilities("sweagent")
    assert manifest["extensions"]["repo_task_agent"] is True


def test_sweagent_manifest_sessions_mode_is_oneshot():
    """sessions.mode == 'oneshot' is BOUND (Run 027 / HA-4 — headless --no-session)."""
    manifest = get_capabilities("sweagent")
    assert manifest["sessions"]["mode"] == "oneshot"


def test_sweagent_manifest_execution_headless_is_true():
    """execution.headless == True (sweagent run is the headless one-shot)."""
    manifest = get_capabilities("sweagent")
    assert manifest["execution"]["headless"] is True
    assert manifest["execution"]["terminal"] is False
    assert manifest["execution"]["interactive"] is False


def test_aider_manifest_has_exactly_eight_groups():
    """get_capabilities('aider') returns EXACTLY eight contract groups."""
    manifest = get_capabilities("aider")
    expected = {"execution", "workspace", "sessions", "extensions",
                "automation", "concurrency", "lifecycle", "models"}
    assert set(manifest.keys()) == expected


def test_aider_manifest_extensions_git_aware_is_true():
    """extensions.git_aware == True (ROADMAP §3 — SPECIALIZED git-integrated signal)."""
    manifest = get_capabilities("aider")
    assert manifest["extensions"]["git_aware"] is True


def test_aider_manifest_extensions_patch_output_is_true():
    """extensions.patch_output == True (ROADMAP §3 — SPECIALIZED patch-emitting signal)."""
    manifest = get_capabilities("aider")
    assert manifest["extensions"]["patch_output"] is True


def test_aider_manifest_sessions_mode_is_oneshot():
    """sessions.mode == 'oneshot' is BOUND (aider --message --yes-always is one-shot)."""
    manifest = get_capabilities("aider")
    assert manifest["sessions"]["mode"] == "oneshot"


def test_aider_manifest_automation_non_interactive_is_true():
    """automation.non_interactive == True (aider --yes-always is bound)."""
    manifest = get_capabilities("aider")
    assert manifest["automation"]["non_interactive"] is True
    assert manifest["automation"]["deterministic_exit"] is True


# ── E. enumeration (D3) ────────────────────────────────────────────────


def test_experimental_harnesses_lists_sweagent_and_aider():
    """EXPERIMENTAL_HARNESSES is exactly (sweagent, aider) — the D3 registered set."""
    assert EXPERIMENTAL_HARNESSES == ("sweagent", "aider")


def test_supported_harnesses_does_not_include_experimental_keys():
    """SUPPORTED_HARNESSES still has the seven contract keys (the experimental
    harness keys are NOT promoted into the public supported set — D3)."""
    assert "sweagent" not in SUPPORTED_HARNESSES
    assert "aider" not in SUPPORTED_HARNESSES
    # Seven keys (post-Run 028 / HA-5 + Run 026 / HA-3 + Run 022).
    assert len(SUPPORTED_HARNESSES) == 7


# ── F. env builders ────────────────────────────────────────────────────


def test_sweagent_env_always_returns_three_dir_keys():
    """build_sweagent_env ALWAYS returns the three SWE_AGENT_*_DIR keys."""
    env = build_sweagent_env(model_target=None, cfg=_SweagentCfg())
    assert sorted(env.keys()) == [
        "SWE_AGENT_CONFIG_DIR",
        "SWE_AGENT_TOOLS_DIR",
        "SWE_AGENT_TRAJECTORY_DIR",
    ]


def test_sweagent_env_uses_home_relative_defaults_when_dirs_empty():
    """When the cfg dirs are empty, each env value falls back to a home-relative
    path under ``~/tools/SWE-agent/...`` (TG6 forbids hard-coded user-specific
    home strings so we assert KEYS exist + values are non-empty strings, NOT the
    literal path).
    """
    env = build_sweagent_env(model_target=None, cfg=_SweagentCfg())
    assert "SWE_AGENT_CONFIG_DIR" in env
    assert "SWE_AGENT_TOOLS_DIR" in env
    assert "SWE_AGENT_TRAJECTORY_DIR" in env
    assert isinstance(env["SWE_AGENT_CONFIG_DIR"], str)
    assert isinstance(env["SWE_AGENT_TOOLS_DIR"], str)
    assert isinstance(env["SWE_AGENT_TRAJECTORY_DIR"], str)
    assert env["SWE_AGENT_CONFIG_DIR"].strip() != ""
    assert env["SWE_AGENT_TOOLS_DIR"].strip() != ""
    assert env["SWE_AGENT_TRAJECTORY_DIR"].strip() != ""
    # The home-relative defaults mention the SWE-agent directory.
    for v in env.values():
        assert "SWE-agent" in v


def test_sweagent_env_honors_explicit_dir_overrides():
    """Non-empty cfg dirs override the home-relative defaults."""

    class _Cfg(_SweagentCfg):
        def get_sweagent_config_dir(self):
            return "/srv/cfg"

        def get_sweagent_tools_dir(self):
            return "/srv/tools"

        def get_sweagent_trajectory_dir(self):
            return "/srv/traj"

    env = build_sweagent_env(model_target=None, cfg=_Cfg())
    assert env["SWE_AGENT_CONFIG_DIR"] == "/srv/cfg"
    assert env["SWE_AGENT_TOOLS_DIR"] == "/srv/tools"
    assert env["SWE_AGENT_TRAJECTORY_DIR"] == "/srv/traj"


def test_aider_env_is_always_empty():
    """build_aider_env ALWAYS returns {} (aider reads OPENAI_API_KEY itself;
    no load-bearing child-env override — see build_aider_env docstring)."""
    env = build_aider_env(model_target="m", cfg=_AiderCfg())
    assert env == {}
    # Even when called with a model + a non-empty cfg, still {}.
    env = build_aider_env(model_target="some-model", cfg=_AiderCfg())
    assert env == {}
