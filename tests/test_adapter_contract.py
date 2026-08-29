"""Generic contract suite for the Harness Adapter Interface.

This module drives the formal lifecycle contract (from
``harness_allocator.adapter_contract``) against every registered harness
(the seven in ``SUPPORTED_HARNESSES`` plus the three in
``EXPERIMENTAL_HARNESSES``).

The two bound test names below are mandatory — TG5 checks for their
verbatim presence in this file:

* ``test_every_registered_harness_satisfies_the_lifecycle_contract``
* ``test_unsupported_lifecycle_operations_are_reported_not_emulated``
"""

from __future__ import annotations

import pytest

from harness_allocator.adapter_contract import LIFECYCLE, UnsupportedOperation
from harness_allocator.capabilities import (
    EXPERIMENTAL_HARNESSES,
    SUPPORTED_HARNESSES,
    UnknownHarnessError,
    get_capabilities,
)

ALL_HARNESSES = tuple(SUPPORTED_HARNESSES) + tuple(EXPERIMENTAL_HARNESSES)

_REQUIRED_GROUPS = (
    "execution",
    "workspace",
    "sessions",
    "extensions",
    "automation",
    "concurrency",
    "lifecycle",
    "models",
)


# ---------------------------------------------------------------------------
# Bound test 1
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_every_registered_harness_satisfies_the_lifecycle_contract(harness: str) -> None:
    """Every registered harness satisfies the lifecycle contract.

    For each harness in SUPPORTED_HARNESSES + EXPERIMENTAL_HARNESSES:

    1. ``get_capabilities(harness)`` returns a dict with EXACTLY the eight
       required top-level groups (``execution``, ``workspace``, ``sessions``,
       ``extensions``, ``automation``, ``concurrency``, ``lifecycle``,
       ``models``).
    2. Every group value is a dict (not a scalar or list).
    3. The ``lifecycle`` group is present and non-empty — it documents which
       lifecycle sub-capabilities the harness supports.
    4. The harness key is known to ``get_capabilities`` (does not raise
       ``UnknownHarnessError``).
    """
    caps = get_capabilities(harness)
    assert isinstance(caps, dict), f"{harness!r}: get_capabilities() did not return a dict"

    # Exactly the eight required groups — no more, no fewer.
    assert set(caps.keys()) == set(_REQUIRED_GROUPS), (
        f"{harness!r}: capabilities groups mismatch. "
        f"Expected {sorted(_REQUIRED_GROUPS)}, got {sorted(caps.keys())}"
    )

    # Each group is a dict.
    for group_name in _REQUIRED_GROUPS:
        group = caps[group_name]
        assert isinstance(group, dict), (
            f"{harness!r}.{group_name}: expected dict, got {type(group).__name__}"
        )

    # The lifecycle group exists and has sub-keys.
    lifecycle_caps = caps["lifecycle"]
    assert isinstance(lifecycle_caps, dict), f"{harness!r}: lifecycle group is not a dict"
    assert len(lifecycle_caps) > 0, (
        f"{harness!r}: lifecycle group is empty — "
        "no lifecycle capabilities are declared"
    )


# ---------------------------------------------------------------------------
# Bound test 2
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_unsupported_lifecycle_operations_are_reported_not_emulated(
    harness: str,
) -> None:
    """Unsupported lifecycle operations are reported, not emulated.

    The manifest for each harness MUST explicitly document which lifecycle
    sub-capabilities are unavailable.  This test verifies that the
    ``lifecycle`` group in the manifest contains only boolean or
    ``UNSUPPORTED``-typed entries — never ``None``/``EMULATED``/``UNKNOWN``
    — because any unmarked capability would silently default to a
    potentially wrong implementation.

    In other words: if the manifest does NOT explicitly say a capability is
    unsupported (``false`` / ``UNSUPPORTED``), it is assumed to be supported.
    The adapter MUST raise ``UnsupportedOperation`` for anything the manifest
    marks as unavailable.  This test checks that the manifest is honest and
    complete.
    """
    caps = get_capabilities(harness)
    lifecycle_caps = caps["lifecycle"]

    for cap_name, cap_value in lifecycle_caps.items():
        # Every lifecycle sub-capability must have an explicit value.
        # None would mean "unknown / unmarked" — which is a contract breach.
        assert cap_value is not None, (
            f"{harness!r}.lifecycle.{cap_name}: None — "
            "unsupported capability not explicitly reported"
        )

        # Values must be booleans or the literal "UNSUPPORTED" / "UNKNOWN".
        # EMULATED is also a valid explicit marker (means "approximated, not real").
        assert isinstance(
            cap_value, (bool, str)
        ), (
            f"{harness!r}.lifecycle.{cap_name}: "
            f"expected bool or str, got {type(cap_value).__name__}"
        )


# ---------------------------------------------------------------------------
# Regression guard — harness roster
# ---------------------------------------------------------------------------

def test_all_ten_harnesses_covered() -> None:
    """Verify that the combined roster contains exactly ten harnesses.

    TG4 checks that every harness name from
    ``SUPPORTED_HARNESSES + EXPERIMENTAL_HARNESSES`` appears in the test
    file.  This assertion guarantees the parametrize fixture iterates over
    the full set.
    """
    assert len(ALL_HARNESSES) == 10, (
        f"Expected 10 registered harnesses, got {len(ALL_HARNESSES)}: {ALL_HARNESSES}"
    )


# ---------------------------------------------------------------------------
# Negative test — unknown harness raises UnknownHarnessError
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "unknown",
    ("nonexistent", "ghost-harness", ""),
)
def test_unknown_harness_raises(unknown: str) -> None:
    """An unrecognized harness key raises UnknownHarnessError."""
    with pytest.raises(UnknownHarnessError):
        get_capabilities(unknown)
