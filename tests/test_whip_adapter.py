"""Whip-specific adapter tests (Run 069).

Constraint: whip is pinned at v0.4.0, located at
/home/svend/.local/bin/whip.  This file uses only the public API surface
discovered in prior handoffs — it does NOT read any source files.

Imports the same contract as test_adapter_contract.py so the reviewer
can compare patterns easily.
"""

from __future__ import annotations

import subprocess

import pytest

from harness_allocator.adapter_contract import LIFECYCLE, UnsupportedOperation
from harness_allocator.capabilities import get_capabilities


def test_whip_version_is_0_4_0() -> None:
    """Whip must be exactly v0.4.0 as pinned in the GOAL."""
    result = subprocess.run(
        ["/home/svend/.local/bin/whip", "--version"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    version_str = result.stdout.strip()
    assert "0.4.0" in version_str, (
        f"Expected whip v0.4.0 but got: {version_str}"
    )


def test_whip_capabilities_exist() -> None:
    """Whip adapter must be registered and return a valid capabilities dict."""
    caps = get_capabilities("whip")
    assert caps is not None
    assert isinstance(caps, dict)
    for key in ("execution", "workspace", "concurrency", "lifecycle"):
        assert key in caps, f"whip capabilities missing key: {key}"


def test_whip_capabilities_has_all_eight_groups() -> None:
    """Whip capabilities must have exactly the eight contract groups."""
    caps = get_capabilities("whip")
    expected_groups = {
        "execution",
        "workspace",
        "sessions",
        "extensions",
        "automation",
        "concurrency",
        "lifecycle",
        "models",
    }
    actual_groups = set(caps.keys())
    assert actual_groups == expected_groups, (
        f"whip capabilities groups mismatch: got {actual_groups}, "
        f"expected {expected_groups}"
    )


def test_whip_lifecycle_is_non_empty_dict() -> None:
    """Whip lifecycle group must be a non-empty dict of sub-capabilities."""
    caps = get_capabilities("whip")
    lifecycle = caps.get("lifecycle")
    assert lifecycle is not None, "whip capabilities must have a 'lifecycle' group"
    assert isinstance(lifecycle, dict), (
        f"whip 'lifecycle' must be a dict, got {type(lifecycle).__name__}"
    )
    assert len(lifecycle) > 0, "whip 'lifecycle' must not be empty"
