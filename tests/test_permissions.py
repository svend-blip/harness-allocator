"""Tests for harness_allocator.permissions — D1 deliverable.

Verifies:
  - Three normalized permission modes exist, in order.
  - INTERRUPT_CURRENT_TASK and TERMINATE_HARNESS are distinct.
  - Unenforceable permissions are refused, not silently downgraded.
  - MCP tiers never include untrusted_workspace.
  - Every registered harness answers effective_permission for enforceable modes.
  - Interrupt and terminate are distinct, never aliased operations.
  - Untrusted workspace MCP is never granted execution rights.
"""

from __future__ import annotations

import pytest

from harness_allocator.permissions import (
    PERMISSION_MODES,
    MCP_TRUST_TIERS,
    INTERRUPT_CURRENT_TASK,
    TERMINATE_HARNESS,
    effective_permission,
    mcp_tiers_allowed,
    PermissionRefusedError,
)
from harness_allocator.capabilities import (
    SUPPORTED_HARNESSES,
    EXPERIMENTAL_HARNESSES,
    _MANIFEST_QWEN,
)


# ── TG1: Permission modes ──────────────────────────────────────────────────

class TestPermissionModes:
    """TG1: The three normalized permission modes exist, in order."""

    def test_three_modes_exist(self):
        assert list(PERMISSION_MODES) == [
            "READ_ONLY",
            "WORKSPACE_WRITE",
            "FULL_ACCESS",
        ]

    def test_modes_count_is_three(self):
        assert len(PERMISSION_MODES) == 3

    def test_mcp_trust_tiers_exist(self):
        assert MCP_TRUST_TIERS == (
            "trusted_system",
            "trusted_workspace",
            "untrusted_workspace",
        )


# ── TG3: Interrupt vs terminate ────────────────────────────────────────────

class TestInterruptTerminate:
    """TG3: Interrupt and terminate are distinct constants, never aliased."""

    def test_interrupt_and_terminate_are_distinct_operations(self):
        assert INTERRUPT_CURRENT_TASK != TERMINATE_HARNESS

    def test_interrupt_has_semantic_value(self):
        assert INTERRUPT_CURRENT_TASK == "interrupt_current_task"

    def test_terminate_has_semantic_value(self):
        assert TERMINATE_HARNESS == "terminate_harness"


# ── TG4: Refusal, not downgrade ────────────────────────────────────────────

class TestEffectivePermissionRefusal:
    """TG4: An unenforceable permission is refused, not silently downgraded."""

    def test_an_unenforceable_permission_is_refused_not_downgraded(self):
        """qwen has workspace.read_only=False → READ_ONLY must raise."""
        with pytest.raises(PermissionRefusedError):
            effective_permission("qwen", "READ_ONLY")

    def test_full_access_refused_when_manifest_lacks_full_access(self):
        """codex has full_access=False → FULL_ACCESS must raise."""
        with pytest.raises(PermissionRefusedError):
            effective_permission("codex", "FULL_ACCESS")

    def test_unknown_harness_raises(self):
        with pytest.raises(PermissionRefusedError):
            effective_permission("nonexistent", "WORKSPACE_WRITE")

    def test_unknown_mode_raises(self):
        with pytest.raises(PermissionRefusedError):
            effective_permission("codex", "BOGUS_MODE")


# ── TG5: Every harness answers WORKSPACE_WRITE ─────────────────────────────

class TestEveryHarnessAnswers:
    """TG5: Every registered harness answers effective_permission
    for an enforceable mode."""

    def test_every_registered_harness_answers_effective_permission(self):
        all_harnesses = list(SUPPORTED_HARNESSES) + list(EXPERIMENTAL_HARNESSES)
        count = sum(
            1
            for h in all_harnesses
            if effective_permission(h, "WORKSPACE_WRITE") == "WORKSPACE_WRITE"
        )
        assert count == len(all_harnesses)

    def test_codex_workspace_write_succeeds(self):
        assert effective_permission("codex", "WORKSPACE_WRITE") == "WORKSPACE_WRITE"

    def test_read_only_successfully_enforced(self):
        """codex has read_only=True → READ_ONLY succeeds."""
        assert effective_permission("codex", "READ_ONLY") == "READ_ONLY"


# ── TG7: Untrusted workspace MCP ───────────────────────────────────────────

class TestMcpTiers:
    """TG7: Untrusted workspace MCP appears in no mode's allowed tiers."""

    def test_untrusted_workspace_mcp_is_never_granted_execution(self):
        for mode in PERMISSION_MODES:
            tiers = mcp_tiers_allowed(mode)
            assert "untrusted_workspace" not in tiers

    def test_read_only_allows_only_trusted_system(self):
        assert mcp_tiers_allowed("READ_ONLY") == ("trusted_system",)

    def test_workspace_write_allows_two_tiers(self):
        assert mcp_tiers_allowed("WORKSPACE_WRITE") == (
            "trusted_system",
            "trusted_workspace",
        )

    def test_full_access_allows_two_tiers(self):
        assert mcp_tiers_allowed("FULL_ACCESS") == (
            "trusted_system",
            "trusted_workspace",
        )

    def test_unknown_mode_returns_empty_tuple(self):
        assert mcp_tiers_allowed("BOGUS_MODE") == ()
