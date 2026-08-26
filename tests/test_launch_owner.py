"""Tests for ``harness_allocator.launchspec.get_launch_spec`` — D2 of Run 037.

Contract (GOAL.md Run 037 §1 D2, §4 TG1–TG3, and the binding constraints):

- The two registries — ``SUPPORTED_HARNESSES + EXPERIMENTAL_HARNESSES``
  (what the allocator declares) and ``NATIVE_HARNESSES`` (what the
  allocator LAUNCHES) — are bound to each other so they cannot drift
  apart silently. A harness added to one and not the other must fail
  loudly rather than produce a quiet disagreement.
- Every registered harness declares ``launch_owner`` (the SIXTH LaunchSpec
  key added in D1) valued ``"harness_allocator"`` or ``"model_allocator"``:
    - ``"harness_allocator"`` iff the harness is in ``NATIVE_HARNESSES``
      (the allocator builds the launch command itself).
    - ``"model_allocator"`` iff the harness is in
      ``SUPPORTED_HARNESSES ∪ EXPERIMENTAL_HARNESSES`` but NOT in
      ``NATIVE_HARNESSES`` (the allocator describes the harness but
      model-allocator's client adapters build the launch).
- ``launch_owner`` is a ``str`` from the two-value vocabulary
  ``{"harness_allocator", "model_allocator"}`` — never a bool, never None,
  never any other string. (GOAL.md §2 type binding: a criterion that only
  checks truthiness passes on the wrong type.)

The roster is DERIVED from the imported tuples — never a hand-listed
harness name tuple. A hand-listed test roster silently stops covering the
next adapter someone registers, which is exactly when a binding contract
matters.

Stdlib + pytest only (the package's standing constraint). HERMETIC: no
subprocess, no filesystem existence checks, no network, no env reads, no
live harness launch. ``launchspec`` is a pure declaration module.
"""

import sys
from pathlib import Path

import pytest

# Import the package from the project root (sibling of tests/).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness_allocator.capabilities import (  # noqa: E402
    EXPERIMENTAL_HARNESSES,
    SUPPORTED_HARNESSES,
)
from harness_allocator.definition import NATIVE_HARNESSES  # noqa: E402
from harness_allocator.launchspec import get_launch_spec  # noqa: E402


# ── Derived roster — the contract (GOAL.md §1 D2). NEVER hand-list harness
# names; walk the imported tuples. ───────────────────────────────────────


ALL_HARNESSES = tuple(SUPPORTED_HARNESSES) + tuple(EXPERIMENTAL_HARNESSES)


# ── TG1 — every registered harness declares launch_owner ────────────────


@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_launch_owner_present_on_every_registered_harness(harness):
    """``get_launch_spec(harness)`` carries the ``launch_owner`` key for
    every registered harness (TG1 contract). The roster is DERIVED from
    ``ALL_HARNESSES`` — never a hand-listed tuple of harness names."""
    spec = get_launch_spec(harness)
    assert "launch_owner" in spec, (
        f"launch spec for {harness!r} has keys {sorted(spec.keys())!r}, "
        f"missing the bound 'launch_owner' key"
    )


# ── launch_owner is a str from the two-value vocabulary ─────────────────


_LAUNCH_OWNER_VOCAB = {"harness_allocator", "model_allocator"}


@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_launch_owner_is_str_from_two_value_vocabulary(harness):
    """``launch_owner`` is a ``str`` from the two-value vocabulary
    ``{"harness_allocator", "model_allocator"}`` (GOAL.md §2 type binding).
    A criterion that only checks truthiness passes on the wrong type."""
    spec = get_launch_spec(harness)
    owner = spec["launch_owner"]
    assert isinstance(owner, str), (
        f"launch_owner for {harness!r} is type {type(owner).__name__}, "
        f"expected str"
    )
    assert owner in _LAUNCH_OWNER_VOCAB, (
        f"launch_owner for {harness!r} is {owner!r}, expected one of "
        f"{sorted(_LAUNCH_OWNER_VOCAB)}"
    )


# ── TG2 — launch_owner agrees with NATIVE_HARNESSES for every harness ────


@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_launch_owner_agrees_with_native_harness_membership(harness):
    """``launch_owner == "harness_allocator"`` iff ``harness in
    NATIVE_HARNESSES`` (TG2 contract). The two registries cannot drift
    apart silently."""
    spec = get_launch_spec(harness)
    is_native = harness in NATIVE_HARNESSES
    expected = "harness_allocator" if is_native else "model_allocator"
    assert spec["launch_owner"] == expected, (
        f"launch_owner for {harness!r} is {spec['launch_owner']!r}, "
        f"expected {expected!r} (NATIVE_HARNESSES membership = {is_native})"
    )


# ── The two registries are bound to each other ──────────────────────────


def test_native_harnesses_subset_of_supported_plus_experimental():
    """``NATIVE_HARNESSES ⊆ SUPPORTED_HARNESSES ∪ EXPERIMENTAL_HARNESSES``
    (GOAL.md §1 D2). The allocator cannot natively launch a harness it
    does not also describe."""
    declared = set(ALL_HARNESSES)
    native = set(NATIVE_HARNESSES)
    assert native <= declared, (
        f"NATIVE_HARNESSES has entries {sorted(native - declared)!r} "
        f"that are not in SUPPORTED_HARNESSES + EXPERIMENTAL_HARNESSES; "
        f"the allocator launches {sorted(native - declared)} natively "
        f"without declaring it"
    )


def test_model_allocator_set_equals_difference_of_declared_minus_native():
    """The set of harnesses whose ``launch_owner`` is ``"model_allocator"``
    is EXACTLY ``(SUPPORTED_HARNESSES ∪ EXPERIMENTAL_HARNESSES) −
    NATIVE_HARNESSES``. Every harness declared but not native goes through
    model-allocator; no native harness is misclassified as model-allocator;
    no model-allocator harness is misclassified as native."""
    declared = set(ALL_HARNESSES)
    native = set(NATIVE_HARNESSES)
    expected_model_allocator = declared - native

    actual_model_allocator = {
        h for h in ALL_HARNESSES
        if get_launch_spec(h)["launch_owner"] == "model_allocator"
    }
    assert actual_model_allocator == expected_model_allocator, (
        f"model_allocator set mismatch: declared-but-not-native = "
        f"{sorted(expected_model_allocator)!r}, actual = "
        f"{sorted(actual_model_allocator)!r}"
    )


# ── Sentinel: a non-registered harness name does not bind the registries


def test_unknown_harness_does_not_appear_in_model_allocator_set():
    """A harness name that is not registered is, by definition, not in the
    ``model_allocator`` set — the binding test is on the DERIVED roster,
    not on arbitrary harness-name strings."""
    declared = set(ALL_HARNESSES)
    assert "bogus" not in declared
    model_allocator_set = {
        h for h in ALL_HARNESSES
        if get_launch_spec(h)["launch_owner"] == "model_allocator"
    }
    assert "bogus" not in model_allocator_set


# ── Every LaunchSpec still carries the six bound keys (sanity) ──────────


_LAUNCH_SPEC_EXPECTED_KEYS = {
    "mode",
    "needs_initial_prompt",
    "anchor",
    "required_env",
    "activity_markers",
    "launch_owner",
}


@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_launch_spec_key_set_includes_launch_owner(harness):
    """The LaunchSpec key set is now SIX keys (the run-036 contract said
    five; D1 of Run 037 added ``launch_owner``). This test pins the new
    set so a future removal is caught here rather than at the equality
    assertion in ``tests/test_launchspec.py:82`` (which the run-037 fence
    also admits, on its one permitted line)."""
    spec = get_launch_spec(harness)
    assert "launch_owner" in set(spec.keys()), (
        f"launch spec for {harness!r} is missing 'launch_owner'; "
        f"the new six-key contract is not satisfied"
    )
    assert _LAUNCH_SPEC_EXPECTED_KEYS <= set(spec.keys()), (
        f"launch spec for {harness!r} has keys {sorted(spec.keys())!r}, "
        f"missing at least one of the expected keys "
        f"{sorted(_LAUNCH_SPEC_EXPECTED_KEYS - set(spec.keys()))!r}"
    )
