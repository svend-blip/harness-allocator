"""Tests for ``harness_allocator.runspec`` — D2 of Run 020 (HA-1).

Contract (GOAL.md Run 020 §1 D2, §4 TG2 / TG6, and the binding constraints):

- ``HarnessRunSpec`` and ``HarnessRunResult`` are importable from
  ``harness_allocator.runspec`` (TG2's contract, asserted directly).
- ``HarnessRunSpec.validate()``:
    * returns None on success (a valid spec);
    * raises ``UnknownHarnessError`` for ``harness="bogus"``
      (and the error IS a ``ValueError`` subclass);
    * raises ``MissingPromptError`` for ``prompt=""`` AND ``prompt="   "``;
    * raises ``UnsupportedCapabilityError`` for unsupported capability names
      (e.g. ``codex`` with ``headless`` — codex.execution.headless is False;
      a made-up name like ``flux_capacitor`` for ``dsh``);
    * does NOT raise for a supported capability (e.g. ``dsh`` with
      ``headless``, ``mcp``, ``interrupt_safe`` — all True in the dsh manifest).
- The three typed errors are importable from ``harness_allocator.capabilities``
  AND all are ``ValueError`` subclasses.
- ``HarnessRunResult`` defaults:
    * ``exit_code`` is None;
    * ``session_id`` is None;
    * ``usage.input_tokens`` / ``usage.output_tokens`` / ``usage.cost`` are None;
    * ``timing.started_at`` / ``timing.finished_at`` are None;
    * ``evidence.changed_files`` is an empty list.

Stdlib + pytest only (the package's standing constraint).
"""

import sys
from pathlib import Path

import pytest

# Import the package from the project root (sibling of tests/).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness_allocator.capabilities import (  # noqa: E402
    LargeInputRefusedError,
    MissingPromptError,
    SUPPORTED_HARNESSES,
    UnknownHarnessError,
    UnsupportedCapabilityError,
    get_capabilities,
)
from harness_allocator.runspec import (  # noqa: E402
    HarnessRunResult,
    HarnessRunSpec,
    RunEvidence,
    RunOutput,
    RunPermissions,
    RunRequirements,
    RunSession,
    RunTiming,
    RunUsage,
)


# ── TG2 — import surface ──────────────────────────────────────────────


def test_runspec_imports():
    """``HarnessRunSpec`` and ``HarnessRunResult`` import from
    ``harness_allocator.runspec`` (TG2's contract)."""
    from harness_allocator import runspec

    assert runspec.HarnessRunSpec is HarnessRunSpec
    assert runspec.HarnessRunResult is HarnessRunResult


def test_typed_errors_importable_from_capabilities():
    """The three D2 typed errors are importable from
    ``harness_allocator.capabilities`` (D1 bound them; D2 imports them).
    """
    import harness_allocator.capabilities as cap

    assert cap.UnknownHarnessError is UnknownHarnessError
    assert cap.MissingPromptError is MissingPromptError
    assert cap.UnsupportedCapabilityError is UnsupportedCapabilityError


def test_typed_errors_are_value_error_subclasses():
    """All D1 typed errors are ``ValueError`` subclasses — the package idiom
    so legacy ``except ValueError`` callers keep working."""
    assert issubclass(UnknownHarnessError, ValueError)
    assert issubclass(MissingPromptError, ValueError)
    assert issubclass(UnsupportedCapabilityError, ValueError)
    # Bound in D1 but unused in D2 — sanity-check it still exists and is a
    # ValueError subclass (handoff 3 raises it).
    assert issubclass(LargeInputRefusedError, ValueError)


# ── validate() — success path ─────────────────────────────────────────


def test_validate_success_returns_none():
    """A valid spec returns None from validate() (no exception)."""
    spec = HarnessRunSpec(harness="dsh", prompt="do a thing")
    assert spec.validate() is None


def test_validate_success_with_supported_capabilities():
    """A spec with supported capabilities does NOT raise."""
    spec = HarnessRunSpec(
        harness="dsh",
        prompt="do a thing",
        requirements=RunRequirements(
            capabilities=["headless", "mcp", "interrupt_safe"],
        ),
    )
    assert spec.validate() is None


# ── validate() — UnknownHarnessError ──────────────────────────────────


def test_validate_unknown_harness_raises_typed_error():
    """validate() raises UnknownHarnessError for an unknown harness key."""
    spec = HarnessRunSpec(harness="bogus", prompt="anything")
    with pytest.raises(UnknownHarnessError) as exc_info:
        spec.validate()
    # The error message must name the unknown harness (reviewer can see
    # it without having to read the call site).
    assert "bogus" in str(exc_info.value)


def test_validate_empty_harness_raises_typed_error():
    """Empty string is not a valid harness key — must raise the typed error."""
    spec = HarnessRunSpec(harness="", prompt="anything")
    with pytest.raises(UnknownHarnessError):
        spec.validate()


@pytest.mark.parametrize("harness", ["codex", "claude-code", "opencode", "dsh"])
def test_validate_accepts_every_supported_harness(harness):
    """All four SUPPORTED_HARNESSES values pass the harness check."""
    spec = HarnessRunSpec(harness=harness, prompt="anything")
    assert spec.validate() is None


# ── validate() — MissingPromptError ───────────────────────────────────


def test_validate_missing_prompt_raises_typed_error():
    """validate() raises MissingPromptError for prompt=""."""
    spec = HarnessRunSpec(harness="dsh", prompt="")
    with pytest.raises(MissingPromptError) as exc_info:
        spec.validate()
    assert "prompt" in str(exc_info.value).lower()


def test_validate_whitespace_prompt_raises_typed_error():
    """validate() raises MissingPromptError for prompt="   " (whitespace-only)."""
    spec = HarnessRunSpec(harness="dsh", prompt="   ")
    with pytest.raises(MissingPromptError):
        spec.validate()


# ── validate() — UnsupportedCapabilityError ────────────────────────────


def test_validate_unsupported_capability_codex_headless_raises():
    """validate() raises UnsupportedCapabilityError when codex is asked
    for ``headless`` (codex.execution.headless is False in the D1 manifest)."""
    spec = HarnessRunSpec(
        harness="codex",
        prompt="anything",
        requirements=RunRequirements(capabilities=["headless"]),
    )
    with pytest.raises(UnsupportedCapabilityError) as exc_info:
        spec.validate()
    msg = str(exc_info.value)
    # The error message names both the harness and the capability.
    assert "codex" in msg
    assert "headless" in msg


def test_validate_unsupported_capability_dsh_flux_capacitor_raises():
    """validate() raises UnsupportedCapabilityError for a made-up
    capability name on dsh."""
    spec = HarnessRunSpec(
        harness="dsh",
        prompt="anything",
        requirements=RunRequirements(capabilities=["flux_capacitor"]),
    )
    with pytest.raises(UnsupportedCapabilityError) as exc_info:
        spec.validate()
    assert "flux_capacitor" in str(exc_info.value)


def test_validate_string_valued_leaf_is_not_a_capability():
    """String-valued leaves (sessions.mode) are NOT capability names.
    Asking for ``mode`` on dsh must raise UnsupportedCapabilityError."""
    spec = HarnessRunSpec(
        harness="dsh",
        prompt="anything",
        requirements=RunRequirements(capabilities=["mode"]),
    )
    with pytest.raises(UnsupportedCapabilityError):
        spec.validate()


def test_validate_false_valued_leaf_is_not_supported():
    """A leaf whose value is False in the manifest is unsupported.
    ``codex.extensions.custom_tools`` is False → asking for it must raise."""
    spec = HarnessRunSpec(
        harness="codex",
        prompt="anything",
        requirements=RunRequirements(capabilities=["custom_tools"]),
    )
    with pytest.raises(UnsupportedCapabilityError):
        spec.validate()


# ── validate() — capability support rule (every True-valued leaf) ─────


@pytest.mark.parametrize(
    "harness, cap",
    [
        # dsh: headless=True, mcp=True, interrupt_safe=True, etc.
        ("dsh", "headless"),
        ("dsh", "mcp"),
        ("dsh", "interrupt_safe"),
        ("dsh", "non_interactive"),
        ("dsh", "deterministic_exit"),
        ("dsh", "workspace_write"),
        # codex: mcp=True, persistent_session=True, etc.
        ("codex", "mcp"),
        ("codex", "persistent_session"),
        ("codex", "terminal"),
        ("codex", "interactive"),
        ("codex", "session_resume"),
        # claude-code: same shape as codex.
        ("claude-code", "mcp"),
        ("claude-code", "persistent_session"),
        # opencode: same shape as codex.
        ("opencode", "mcp"),
        ("opencode", "persistent_session"),
    ],
)
def test_validate_supports_every_true_valued_leaf(harness, cap):
    """For every True-valued leaf in the manifest, validate() accepts it
    as a required capability name."""
    spec = HarnessRunSpec(
        harness=harness,
        prompt="anything",
        requirements=RunRequirements(capabilities=[cap]),
    )
    assert spec.validate() is None


# ── validate() — error ordering ───────────────────────────────────────


def test_validate_error_order_unknown_harness_before_prompt():
    """UnknownHarnessError is raised BEFORE MissingPromptError — a bogus
    harness with an empty prompt should fail on harness first."""
    spec = HarnessRunSpec(harness="bogus", prompt="")
    with pytest.raises(UnknownHarnessError):
        spec.validate()


def test_validate_error_order_unknown_harness_before_capability():
    """UnknownHarnessError is raised BEFORE UnsupportedCapabilityError."""
    spec = HarnessRunSpec(
        harness="bogus",
        prompt="anything",
        requirements=RunRequirements(capabilities=["flux_capacitor"]),
    )
    with pytest.raises(UnknownHarnessError):
        spec.validate()


def test_validate_error_order_prompt_before_capability():
    """MissingPromptError is raised BEFORE UnsupportedCapabilityError — an
    empty prompt with an unsupported capability fails on prompt first."""
    spec = HarnessRunSpec(
        harness="dsh",
        prompt="",
        requirements=RunRequirements(capabilities=["flux_capacitor"]),
    )
    with pytest.raises(MissingPromptError):
        spec.validate()


# ── HarnessRunSpec defaults ──────────────────────────────────────────


def test_runspec_defaults_are_empty():
    """HarnessRunSpec default values are empty / unset; callers populate."""
    spec = HarnessRunSpec()
    assert spec.request_id == ""
    assert spec.role == ""
    assert spec.harness == ""
    assert spec.working_directory == ""
    assert spec.prompt == ""
    assert spec.model_reference == ""
    assert spec.environment == {}
    assert spec.timeout is None


def test_runspec_nested_helpers_default_correctly():
    """Nested helpers default to their declared defaults (no shared state)."""
    spec = HarnessRunSpec()
    assert isinstance(spec.permissions, RunPermissions)
    assert spec.permissions.mode == ""
    assert isinstance(spec.session, RunSession)
    assert spec.session.mode == ""
    assert spec.session.session_id == ""
    assert isinstance(spec.requirements, RunRequirements)
    assert spec.requirements.capabilities == []
    assert isinstance(spec.output, RunOutput)
    assert spec.output.expected_artifacts == []


def test_runspec_nested_helpers_do_not_share_mutable_state():
    """Two specs must not share nested-helper instances — default_factory
    is a class attribute (each call creates a new one)."""
    s1 = HarnessRunSpec()
    s2 = HarnessRunSpec()
    assert s1.permissions is not s2.permissions
    assert s1.session is not s2.session
    assert s1.requirements is not s2.requirements
    assert s1.environment is not s2.environment
    assert s1.output is not s2.output


# ── HarnessRunResult defaults ─────────────────────────────────────────


def test_runresult_defaults():
    """HarnessRunResult defaults match ROADMAP §5 + GOAL §1 D2 nullability
    contract: exit_code None, session_id None, telemetry counters None,
    evidence.changed_files empty list."""
    result = HarnessRunResult()
    assert result.request_id == ""
    assert result.harness == ""
    assert result.status == ""
    assert result.exit_code is None
    assert result.session_id is None
    assert result.stdout == ""
    assert result.stderr == ""
    assert result.artifacts == []
    assert isinstance(result.evidence, RunEvidence)
    assert result.evidence.changed_files == []
    assert result.evidence.patch_available is False
    assert result.evidence.tests_run == []
    assert isinstance(result.usage, RunUsage)
    assert result.usage.input_tokens is None
    assert result.usage.output_tokens is None
    assert result.usage.cost is None
    assert isinstance(result.timing, RunTiming)
    assert result.timing.started_at is None
    assert result.timing.finished_at is None


def test_runresult_does_not_share_mutable_state():
    """Two HarnessRunResult instances must not share nested lists."""
    r1 = HarnessRunResult()
    r2 = HarnessRunResult()
    assert r1.artifacts is not r2.artifacts
    assert r1.evidence is not r2.evidence
    assert r1.evidence.changed_files is not r2.evidence.changed_files
    assert r1.evidence.tests_run is not r2.evidence.tests_run


# ── Manifest sanity cross-check ───────────────────────────────────────


def test_manifest_sanity_no_string_leaves_in_capability_names():
    """The D1 manifest must not contain any True-valued string leaves —
    string leaves like ``sessions.mode`` must be excluded from the
    supported capability set."""
    for harness in ("codex", "claude-code", "opencode", "dsh"):
        manifest = get_capabilities(harness)
        for group_name, group in manifest.items():
            assert isinstance(group, dict), (
                f"{harness}.{group_name} is not a dict"
            )
            for leaf_name, leaf_value in group.items():
                if isinstance(leaf_value, str):
                    # String-valued leaves are NEVER capability names.
                    assert leaf_value in ("fresh", "resident", "oneshot"), (
                        f"{harness}.{group_name}.{leaf_name} = "
                        f"{leaf_value!r} is a string but not a known "
                        f"mode value"
                    )
