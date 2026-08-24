"""Tests for the Codex profile selector (Run 024 / D1).

The codex adapter must honour a PROFILE SELECTOR so the implementer role
can be relaunched with a GPU-capable sandbox. The contract (GOAL.md §2):

  - Env ``CODEX_PROFILE`` / ini ``[harness] codex_profile`` select the profile.
    Empty / absent = TODAY'S launch byte-identical.
  - Profile ``gpu`` overrides the sandbox mode to
    ``get_codex_profile_gpu_sandbox()`` (default ``danger-full-access``) and
    APPENDS each non-empty gpu add-dir as ``--add-dir <dir>`` after the base
    add-dirs. The ``--ask-for-approval`` line and everything else stay the same.
  - stdlib only; no hardcoded /home/svend paths (values live in ini/env).
  - Profile-less path is BYTE-IDENTICAL — the 272 existing tests in
    tests/test_harness_allocator.py must stay green. The profile-less adapter
    path is read DEFENSIVELY (``getattr`` + ``lambda: ""``) so the existing
    ``_FakeCfg`` test double (which has NO ``get_codex_profile``) keeps working.

Test-name naming contract (TG2 -k filter): tests for the profile-less and
golden paths MUST contain the substring ``profileless`` or ``golden``.
"""

from __future__ import annotations

import importlib

import pytest

from harness_allocator import adapter


# ── Hermetic fake configs (mirror tests/test_harness_allocator.py _FakeCfg) ──


class _FakeCfgProfileLess:
    """Profile-less fake. Mirrors the existing _FakeCfg (no profile method)."""

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


class _FakeCfgGpu:
    """Profile-gpu fake: returns danger-full-access sandbox + add-dirs."""

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

    def get_codex_profile(self):
        return "gpu"

    def get_codex_profile_gpu_sandbox(self):
        return "danger-full-access"

    def get_codex_profile_gpu_add_dirs(self):
        return ["/tmp/extra"]

    def get_dsh_bin(self):
        return "npx @deepseek-ai/dsh"

    def get_dsh_profile(self):
        return "headless"

    def get_dsh_patch_path(self):
        return "/tmp/dsh-v4-pro.patch.yml"


# ── HA-1 literal (the byte-for-byte ratchet) ────────────────────────────

_HA1_LITERAL = [
    "codex",
    "-m",
    "MiniMax-M3",
    "--sandbox",
    "workspace-write",
    "--ask-for-approval",
    "never",
]


# ── A. PROFILE-LESS BYTE-IDENTITY (GOLDEN) ─────────────────────────────


def test_codex_argv_profileless_byte_identical_golden_HA1_literal():
    """Profile-less argv is the HA-1 literal, byte-for-byte.

    The fake has NO ``get_codex_profile`` method — the adapter must read
    defensively (``getattr`` + ``lambda: ""``) so this test double survives
    without an AttributeError. This is the load-bearing ratchet: the existing
    272 tests in tests/test_harness_allocator.py use this exact shape.
    """
    argv = adapter.build_launch_argv(
        "codex", model_target="MiniMax-M3", cfg=_FakeCfgProfileLess()
    )

    assert argv == _HA1_LITERAL, (
        f"profile-less argv must equal HA-1 literal byte-for-byte; "
        f"got {argv!r}"
    )


# ── B. GPU-PROFILE GOLDEN ──────────────────────────────────────────────


def test_codex_argv_gpu_profile_golden_argv_renders_danger_full_access():
    """GPU-profile argv uses danger-full-access sandbox + appended add-dirs.

    The fake's ``get_codex_profile()`` returns ``"gpu"``, ``get_codex_profile_gpu_sandbox()``
    returns ``"danger-full-access"``, and ``get_codex_profile_gpu_add_dirs()`` returns
    ``["/tmp/extra"]``. The argv must override the workspace-write sandbox with
    danger-full-access AND append ``--add-dir /tmp/extra`` AFTER the (empty) base
    add-dirs. The rest of the shape stays the same.
    """
    argv = adapter.build_launch_argv(
        "codex", model_target="MiniMax-M3", cfg=_FakeCfgGpu()
    )

    # Sandbox mode override is the load-bearing gpu signal.
    assert "--sandbox" in argv, f"argv must contain --sandbox; got {argv!r}"
    sandbox_idx = argv.index("--sandbox")
    assert argv[sandbox_idx + 1] == "danger-full-access", (
        f"gpu profile must render --sandbox danger-full-access; got "
        f"{argv[sandbox_idx + 1]!r}"
    )

    # The gpu add-dir is APPENDED.
    assert "--add-dir" in argv, f"argv must contain --add-dir; got {argv!r}"
    add_dir_indices = [i for i, x in enumerate(argv) if x == "--add-dir"]
    add_dir_values = [argv[i + 1] for i in add_dir_indices]
    assert "/tmp/extra" in add_dir_values, (
        f"gpu profile must append --add-dir /tmp/extra; got add_dirs "
        f"{add_dir_values!r}"
    )

    # The other flags stay where HA-1 put them.
    assert argv[0] == "codex"
    assert argv[1:3] == ["-m", "MiniMax-M3"]
    assert argv[-2:] == ["--ask-for-approval", "never"], (
        f"approval policy must be preserved; got {argv[-2:]!r}"
    )


# ── C. ENV-SELECTOR PRECEDENCE ─────────────────────────────────────────


def test_codex_argv_env_profile_takes_precedence_when_ini_fallback_absent(monkeypatch):
    """CODEX_PROFILE env wins; absent env + empty ini = profile-less path.

    With ``CODEX_PROFILE=gpu`` set, the real config's ``get_codex_profile()``
    must return ``"gpu"`` (env takes precedence over the ini fallback).
    Without ``CODEX_PROFILE`` set and no ini fallback, it must return ``""``
    so the profile-less path produces the HA-1 literal.
    """
    import harness_allocator.config as cfg_mod

    # Reload the module to pick up the new getters — but the config reads
    # env/ini lazily through the getters themselves, so a fresh import is
    # enough to surface ``get_codex_profile``.
    importlib.reload(cfg_mod)
    import harness_allocator.adapter as adapter_mod
    importlib.reload(adapter_mod)

    # ENV wins: CODEX_PROFILE=gpu -> gpu profile.
    monkeypatch.setenv("CODEX_PROFILE", "gpu")
    assert cfg_mod.get_codex_profile() == "gpu", (
        f"CODEX_PROFILE=gpu must surface as 'gpu' from get_codex_profile(); "
        f"got {cfg_mod.get_codex_profile()!r}"
    )

    # ABSENT env + empty ini -> profile-less (HA-1 literal).
    monkeypatch.delenv("CODEX_PROFILE", raising=False)
    assert cfg_mod.get_codex_profile() == "", (
        f"absent CODEX_PROFILE + empty ini must surface as '' from "
        f"get_codex_profile(); got {cfg_mod.get_codex_profile()!r}"
    )


# ── D. GPU SANDBOX OVERRIDE (TG1 PROBE) ──────────────────────────────


def test_codex_argv_gpu_profile_via_env_uses_danger_full_access_real_config(monkeypatch):
    """TG1 probe: with CODEX_PROFILE=gpu, the real-config argv uses danger-full-access.

    This is exactly what TG1 in GOAL.md probes. The adapter reads the real
    config module (no injected cfg) and the env selector flips the sandbox
    mode to danger-full-access so the gpu-gated proofs can run.
    """
    import harness_allocator.config as cfg_mod
    importlib.reload(cfg_mod)
    import harness_allocator.adapter as adapter_mod
    importlib.reload(adapter_mod)

    monkeypatch.setenv("CODEX_PROFILE", "gpu")

    argv = adapter_mod.build_launch_argv("codex", "MiniMax-M3", None)

    assert "--sandbox" in argv, f"argv must contain --sandbox; got {argv!r}"
    sandbox_idx = argv.index("--sandbox")
    assert argv[sandbox_idx + 1] == "danger-full-access", (
        f"CODEX_PROFILE=gpu with real config must render --sandbox "
        f"danger-full-access; got {argv[sandbox_idx + 1]!r}"
    )


# ── E. PROFILE-LESS WITH REAL CONFIG (ratchet) ────────────────────────


def test_codex_argv_no_profile_env_keeps_workspace_write_real_config(monkeypatch):
    """No CODEX_PROFILE env -> real-config argv keeps workspace-write sandbox.

    Ratchet for the profile-less path: a missing or empty CODEX_PROFILE
    must NOT change the sandbox from workspace-write to anything else. This
    is the same byte-identical guarantee as the 272 existing HA-1 tests,
    asserted against the REAL config (not a fake).
    """
    import harness_allocator.config as cfg_mod
    importlib.reload(cfg_mod)
    import harness_allocator.adapter as adapter_mod
    importlib.reload(adapter_mod)

    monkeypatch.delenv("CODEX_PROFILE", raising=False)

    argv = adapter_mod.build_launch_argv("codex", "MiniMax-M3", None)

    # The sandbox must stay workspace-write — the load-bearing ratchet.
    assert "--sandbox" in argv, f"argv must contain --sandbox; got {argv!r}"
    sandbox_idx = argv.index("--sandbox")
    assert argv[sandbox_idx + 1] == "workspace-write", (
        f"absent CODEX_PROFILE must keep --sandbox workspace-write; got "
        f"{argv[sandbox_idx + 1]!r}"
    )

    # And the rest of the HA-1 literal is intact.
    assert argv[0] == "codex"
    assert argv[1:3] == ["-m", "MiniMax-M3"]
    assert argv[-2:] == ["--ask-for-approval", "never"]
