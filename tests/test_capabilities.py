"""Tests for ``harness_allocator.capabilities`` — D1 of Run 020 (HA-1).

Contract (GOAL.md Run 020 §1 D1, §4 TG1, and the binding constraints):

- ``get_capabilities(harness)`` returns a dict with EXACTLY five groups
  (``execution``, ``workspace``, ``sessions``, ``extensions``, ``automation``)
  for each of the four supported harnesses
  (``codex``, ``claude-code``, ``opencode``, ``dsh``).
- ``sessions.mode`` values are bound: codex "fresh", claude-code "resident",
  opencode "resident", dsh "oneshot".
- codex: ``extensions["mcp"]`` is True and ``sessions["persistent_session"]``
  is True.
- dsh: ``automation["interrupt_safe"]`` is True and ``execution["headless"]``
  is True.
- ``get_capabilities("bogus")`` raises ``UnknownHarnessError``, which IS a
  subclass of ``ValueError``.
- All four typed errors are ``ValueError`` subclasses and importable from
  ``harness_allocator.capabilities``.

Stdlib + pytest only (the package's standing constraint). These tests do NOT
use the GOAL's ``*refus*`` naming contract — that contract belongs to handoff
3 (execute_spec capability-refusal tests, filtered by TG7's ``-k refus``).
"""

import sys
from pathlib import Path

import pytest

# Import the package from the project root (sibling of tests/).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness_allocator.capabilities import (  # noqa: E402
    EXPERIMENTAL_HARNESSES,
    LargeInputRefusedError,
    MissingPromptError,
    SUPPORTED_HARNESSES,
    UnknownHarnessError,
    UnsupportedCapabilityError,
    get_capabilities,
)


# ── TG1 — every supported harness has all five groups ──────────────────


@pytest.mark.parametrize("harness", ["codex", "claude-code", "opencode", "dsh"])
def test_get_capabilities_returns_all_five_groups(harness):
    """Each of the four harnesses exposes a manifest with EXACTLY the five
    contract groups (TG1 contract)."""
    manifest = get_capabilities(harness)
    expected_groups = {"execution", "workspace", "sessions", "extensions", "automation"}
    assert set(manifest.keys()) == expected_groups, (
        f"manifest for {harness!r} has groups {sorted(manifest.keys())!r}, "
        f"expected exactly {sorted(expected_groups)!r}"
    )


# ── sessions.mode is bound per harness ─────────────────────────────────


@pytest.mark.parametrize(
    "harness, expected_mode",
    [
        ("codex", "fresh"),
        ("claude-code", "resident"),
        ("opencode", "resident"),
        ("dsh", "oneshot"),
    ],
)
def test_sessions_mode_is_bound(harness, expected_mode):
    """``sessions.mode`` is BOUND: codex=fresh, claude-code=resident,
    opencode=resident, dsh=oneshot (Run 020 §1 D1)."""
    manifest = get_capabilities(harness)
    assert manifest["sessions"]["mode"] == expected_mode


# ── codex: mcp and persistent_session ──────────────────────────────────


def test_codex_extensions_mcp_is_true():
    """codex: extensions.mcp = True (grounded in
    adapter.build_codex_mcp_setup_argv — ``codex mcp add``)."""
    manifest = get_capabilities("codex")
    assert manifest["extensions"]["mcp"] is True


def test_codex_sessions_persistent_session_is_true():
    """codex: sessions.persistent_session = True (grounded in
    adapter._codex_argv — the resident TUI launch)."""
    manifest = get_capabilities("codex")
    assert manifest["sessions"]["persistent_session"] is True


# ── dsh: interrupt_safe and headless ───────────────────────────────────


def test_dsh_automation_interrupt_safe_is_true():
    """dsh: automation.interrupt_safe = True (grounded in invoke.run_argv's
    measured SIGINT → SIGTERM → SIGKILL process-group cancel path)."""
    manifest = get_capabilities("dsh")
    assert manifest["automation"]["interrupt_safe"] is True


def test_dsh_execution_headless_is_true():
    """dsh: execution.headless = True (grounded in adapter.build_dsh_argv —
    one-shot headless invocation)."""
    manifest = get_capabilities("dsh")
    assert manifest["execution"]["headless"] is True


# ── Unknown harness → UnknownHarnessError ──────────────────────────────


def test_get_capabilities_unknown_harness_raises_typed_error():
    """A harness key not in SUPPORTED_HARNESSES raises UnknownHarnessError."""
    with pytest.raises(UnknownHarnessError) as exc_info:
        get_capabilities("bogus")
    # The error message must name the unknown harness (reviewer can see it
    # without having to read the call site).
    assert "bogus" in str(exc_info.value)


def test_unknown_harness_error_is_value_error_subclass():
    """UnknownHarnessError IS a subclass of ValueError — the package idiom
    so legacy ``except ValueError`` callers keep working."""
    assert issubclass(UnknownHarnessError, ValueError)


def test_get_capabilities_empty_string_raises_typed_error():
    """Empty string is not a valid harness key — must raise the typed error."""
    with pytest.raises(UnknownHarnessError):
        get_capabilities("")


# ── All four typed errors are ValueError subclasses and importable ─────


@pytest.mark.parametrize(
    "error_class",
    [
        UnknownHarnessError,
        MissingPromptError,
        UnsupportedCapabilityError,
        LargeInputRefusedError,
    ],
)
def test_typed_errors_are_value_error_subclasses(error_class):
    """All four bound error names are ValueError subclasses (Run 020 §1 D1
    "typed errors are ValueError subclasses — the package's idiom")."""
    assert issubclass(error_class, ValueError)


def test_typed_errors_are_importable_from_capabilities_module():
    """The bound error names are importable from
    ``harness_allocator.capabilities`` directly (handoff 2/3 will import them
    from here)."""
    import harness_allocator.capabilities as cap

    assert cap.UnknownHarnessError is UnknownHarnessError
    assert cap.MissingPromptError is MissingPromptError
    assert cap.UnsupportedCapabilityError is UnsupportedCapabilityError
    assert cap.LargeInputRefusedError is LargeInputRefusedError


# ── Manifest shape sanity (no speculative groups/fields) ───────────────


@pytest.mark.parametrize("harness", ["codex", "claude-code", "opencode", "dsh"])
@pytest.mark.parametrize(
    "group, fields",
    [
        ("execution", {"terminal", "headless", "interactive"}),
        ("workspace", {"read_only", "workspace_write", "full_access"}),
        (
            "sessions",
            {"persistent_session", "session_resume", "mode"},
        ),
        # The three specialized keys were formalized as UNIVERSAL at the
        # HA-4 exit: every harness declares all six, and one that lacks a
        # capability says False rather than omitting the key. The
        # exactness of this assertion is the point and is unchanged —
        # only the contracted field set grew.
        ("extensions", {"skills", "mcp", "custom_tools",
                        "repo_task_agent", "git_aware", "patch_output"}),
        (
            "automation",
            {"non_interactive", "deterministic_exit", "interrupt_safe"},
        ),
    ],
)
def test_manifest_group_has_exact_fields(harness, group, fields):
    """Each group has EXACTLY the contract fields — no extra fields leak in
    (Run 020 §1 D1: "values grounded in measured behavior" — no speculative
    modeling, ROADMAP §3's rule)."""
    manifest = get_capabilities(harness)
    assert set(manifest[group].keys()) == fields


# ── Manifests are independent (no shared mutable state) ────────────────


def test_manifests_do_not_share_mutable_state():
    """Calling get_capabilities twice returns dicts that do NOT share
    group sub-dicts — mutating one must not affect another (or any future
    call)."""
    m1 = get_capabilities("codex")
    m2 = get_capabilities("codex")
    assert m1 is not m2
    for group in ("execution", "workspace", "sessions", "extensions", "automation"):
        assert m1[group] is not m2[group]


# ── SUPPORTED_HARNESSES exposes the four contract keys ─────────────────


def test_supported_harnesses_lists_the_seven_contract_keys():
    """SUPPORTED_HARNESSES exposes the seven contract keys (no extras).

    The 084/085/086 sequence added ``qwen`` to the supported set
    (GOAL.md Run 022 §1 D3 — SUPPORTED_HARNESSES five-key flip), and
    Run 026 / HA-3 added ``goose`` (handoff 122 — SUPPORTED_HARNESSES
    six-key flip), and Run 028 / HA-5 added ``crush`` (handoff 130 —
    SUPPORTED_HARNESSES seven-key flip, the chat-style portabilty-test
    harness). The four existing harnesses' parametrized lists elsewhere
    in this file remain untouched (they test the four existing harnesses,
    not the SUPPORTED_HARNESSES constant).
    """
    assert set(SUPPORTED_HARNESSES) == {
        "codex", "claude-code", "opencode", "dsh", "qwen", "goose", "crush"
    }


# ── The specialized keys are universal (HA-4 exit decision) ────────────


# Derived from the registries, never listed by hand: a hardcoded roster is a
# test that silently stops covering the next harness someone adds, which is
# exactly when a universality contract is worth having.
ALL_HARNESSES = tuple(SUPPORTED_HARNESSES) + tuple(EXPERIMENTAL_HARNESSES)
SPECIALIZED_KEYS = ("repo_task_agent", "git_aware", "patch_output")


@pytest.mark.parametrize("harness", ALL_HARNESSES)
@pytest.mark.parametrize("key", SPECIALIZED_KEYS)
def test_every_harness_answers_the_specialized_keys(harness, key):
    """Every harness declares all three — absence is not an allowed answer.

    A consumer choosing between a chat-style harness and a repo-task or
    patch-emitting one has to be able to ask any of them and get a boolean.
    An optional key answers "absent", and the caller then has to decide
    whether that means False — a reading this project has had falsified
    before. Formalized by Human decision at the HA-4 exit, 2026-08-25.
    """
    extensions = get_capabilities(harness)["extensions"]
    assert key in extensions, f"{harness} does not declare {key}"
    assert isinstance(extensions[key], bool)


def test_the_specialized_keys_are_true_only_where_measured():
    """Honest values, not a blanket default.

    sweagent operates on a repo under a deployment; aider emits patches and
    manages git itself. The six chat-style harnesses do neither, and say so.
    """
    assert get_capabilities("sweagent")["extensions"]["repo_task_agent"] is True
    assert get_capabilities("aider")["extensions"]["git_aware"] is True
    assert get_capabilities("aider")["extensions"]["patch_output"] is True
    specialized = {"sweagent", "aider"}
    for harness in (h for h in ALL_HARNESSES if h not in specialized):
        extensions = get_capabilities(harness)["extensions"]
        assert extensions["repo_task_agent"] is False
        assert extensions["git_aware"] is False
        assert extensions["patch_output"] is False
