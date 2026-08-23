"""RunSpec / RunResult — the normalized request and result dataclasses (D2 of Run 020).

Source of truth:
- GOAL.md Run 020 §1 D2 (the Mission Contract — field names are bound here).
- ROADMAP §4 (the suggested HarnessRunSpec field shape) and §5 (the suggested
  HarnessRunResult field shape).
- ``harness_allocator/capabilities`` (D1, APPROVED — the typed errors and the
  capability manifest this module validates against).

Scope:
- This module is a PURE data/validation layer. It does NOT launch subprocesses,
  does NOT build adapter argv, does NOT import invoke.py or adapter.py.
- ``execute_spec`` (which fills HarnessRunResult from a subprocess) is a
  separate concern in handoff 3.

Design notes:
- ``HarnessRunSpec.validate()`` is the explicit validation entry point (NOT
  ``__post_init__``); a spec can be constructed and validated as two separate
  steps, which lets higher layers attach raw inputs first and validate later.
- The three typed errors raised by ``validate()`` are imported from
  ``harness_allocator.capabilities`` (UnknownHarnessError, MissingPromptError,
  UnsupportedCapabilityError). They are ValueError subclasses, so legacy
  ``except ValueError`` callers keep working.
- ``LargeInputRefusedError`` is bound in D1 but is NOT raised here — it belongs
  to handoff 3's execute_spec.
- ``HarnessRunResult.exit_code`` is BOUND to be int | None, where 0 means
  SUCCESS and None means "not exposed by this harness" or "no successful
  exit". The FILLING of this field is execute_spec's job (handoff 3); this
  module only declares the field. run_argv does not expose the child return
  code, so ``exit_code`` will normally be 0 on SUCCESS and None otherwise.
- Stdlib only (no third-party dependencies).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .capabilities import (
    SUPPORTED_HARNESSES,
    MissingPromptError,
    UnknownHarnessError,
    UnsupportedCapabilityError,
    get_capabilities,
)


# ── Nested helpers (HarnessRunSpec) ────────────────────────────────────


@dataclass
class RunPermissions:
    """The harness's workspace-access mode for this spec.

    ``mode`` is one of: "" (unset), "READ_ONLY", "WORKSPACE_WRITE",
    "FULL_ACCESS". The set of values is intentionally open — this module
    does not validate the literal (validate() does not enforce it); callers
    that route on the value will check the harness's manifest.
    """

    mode: str = ""


@dataclass
class RunSession:
    """The harness's session lifecycle for this spec.

    ``mode`` is one of: "" (unset), "fresh", "resident", "oneshot" — the
    values bound by D1's manifest (HarnessRunSpec.validate() does not
    validate the literal against the manifest either; that's handoff 3's
    concern when it inspects ``spec.session.mode``).
    """

    mode: str = ""
    session_id: str = ""


@dataclass
class RunRequirements:
    """Required capabilities for this spec.

    ``capabilities`` is a list[str] of capability leaf names. Each name is
    checked against the harness's D1 manifest by ``validate()``; the check
    is "the leaf exists AND its value is the boolean True" (string-valued
    leaves like ``sessions.mode`` are never capability names).
    """

    capabilities: list = field(default_factory=list)


@dataclass
class RunOutput:
    """Expected output artifacts for this spec."""

    expected_artifacts: list = field(default_factory=list)


# ── Nested helpers (HarnessRunResult) ──────────────────────────────────


@dataclass
class RunEvidence:
    """Evidence of the harness's effect on the working tree / tests."""

    changed_files: list = field(default_factory=list)
    patch_available: bool = False
    tests_run: list = field(default_factory=list)


@dataclass
class RunUsage:
    """Token / cost usage for the run. Nullable: not every harness exposes
    every counter, and "normalization, not forcing identical telemetry"
    (ROADMAP §5) means missing values stay None rather than zero.
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    cost: float | None = None


@dataclass
class RunTiming:
    """Wall-clock timing for the run. Both fields are float POSIX seconds."""

    started_at: float | None = None
    finished_at: float | None = None


# ── Public spec / result ───────────────────────────────────────────────


@dataclass
class HarnessRunSpec:
    """The normalized, harness-neutral request object (ROADMAP §4 + GOAL §1 D2).

    Field names are BOUND — handoff 3's execute_spec references these by
    attribute name. Default values make every field optional; callers
    populate the relevant ones before calling ``validate()``.
    """

    request_id: str = ""
    role: str = ""
    harness: str = ""
    working_directory: str = ""
    prompt: str = ""
    model_reference: str = ""
    permissions: RunPermissions = field(default_factory=RunPermissions)
    session: RunSession = field(default_factory=RunSession)
    requirements: RunRequirements = field(default_factory=RunRequirements)
    environment: dict = field(default_factory=dict)
    timeout: float | None = None
    output: RunOutput = field(default_factory=RunOutput)

    def validate(self) -> None:
        """Validate the spec; raise typed errors on the first failure.

        Order of checks (BOUND by the handoff):
          (1) UnknownHarnessError if ``self.harness`` is not in
              ``SUPPORTED_HARNESSES``.
          (2) MissingPromptError if ``self.prompt`` is empty / whitespace-only.
          (3) UnsupportedCapabilityError if any name in
              ``self.requirements.capabilities`` is not a True-valued leaf
              in the harness's D1 manifest.

        Returns None on success. Three distinct typed errors so callers
        can react to each independently; all three are ``ValueError``
        subclasses (D1's package idiom).
        """
        harness_key = (self.harness or "").strip()
        if harness_key not in SUPPORTED_HARNESSES:
            raise UnknownHarnessError(
                f"unknown harness: {harness_key!r}"
            )

        if not (self.prompt or "").strip():
            raise MissingPromptError("prompt is required")

        # Capability support rule (BOUND): a capability name is SUPPORTED
        # by a harness iff the manifest has a leaf key (in ANY of the five
        # groups) whose key equals the name AND whose value is True.
        # String-valued leaves (sessions.mode) are never capability names.
        # Absent or False leaves are unsupported.
        if self.requirements.capabilities:
            manifest = get_capabilities(harness_key)
            supported = _supported_capability_names(manifest)
            for name in self.requirements.capabilities:
                if name not in supported:
                    raise UnsupportedCapabilityError(
                        f"harness {harness_key!r} does not support "
                        f"required capability {name!r}"
                    )


@dataclass
class HarnessRunResult:
    """The normalized, harness-neutral result object (ROADMAP §5 + GOAL §1 D2).

    Field names are BOUND — execute_spec fills these in. All fields are
    nullable-tolerant ("normalization, not forcing identical telemetry"):
    a harness that does not expose a value simply leaves the corresponding
    field at its default.

    ``exit_code`` semantics (BOUND): int | None, where 0 means SUCCESS and
    None means "not exposed by this harness" or "the run did not reach a
    well-defined exit". This module only DECLARES the field; execute_spec
    (handoff 3) fills it. ``run_argv`` does not expose the child return
    code, so callers must not depend on a non-None non-zero exit_code —
    the field is 0 on SUCCESS, None otherwise.
    """

    request_id: str = ""
    harness: str = ""
    status: str = ""
    exit_code: int | None = None
    session_id: str | None = None
    stdout: str = ""
    stderr: str = ""
    artifacts: list = field(default_factory=list)
    evidence: RunEvidence = field(default_factory=RunEvidence)
    usage: RunUsage = field(default_factory=RunUsage)
    timing: RunTiming = field(default_factory=RunTiming)


# ── Internal helpers ──────────────────────────────────────────────────


def _supported_capability_names(manifest: dict) -> set:
    """Flatten the five groups of a manifest to the set of True-valued
    boolean leaf names.

    The manifest has exactly the five groups from D1: execution, workspace,
    sessions, extensions, automation. A capability name is the leaf KEY
    whose value is the boolean True. String-valued leaves (sessions.mode)
    are NOT capability names; absent leaves, False leaves, and non-bool
    leaves are all excluded.
    """
    supported = set()
    for group in manifest.values():
        if not isinstance(group, dict):
            continue
        for leaf_name, leaf_value in group.items():
            if leaf_value is True:
                supported.add(leaf_name)
    return supported
