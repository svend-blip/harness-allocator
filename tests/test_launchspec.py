"""Tests for ``harness_allocator.launchspec`` — D4 of Run 036.

Contract (GOAL.md Run 036 §1 D1+D2, §4 TG1–TG4, and the binding constraints):

- ``get_launch_spec(harness)`` returns a dict whose keys are EXACTLY
  ``{mode, needs_initial_prompt, anchor, required_env, activity_markers}``
  for every registered harness (the NINE supported plus the TWO
  experimental). The roster is DERIVED from ``SUPPORTED_HARNESSES +
  EXPERIMENTAL_HARNESSES`` — never a hand-listed tuple.
- ``get_stop_spec(harness)`` returns a dict whose keys are EXACTLY
  ``{signals, grace_seconds, verify}`` for every registered harness.
- Bound vocabularies and types: ``mode`` ∈ {resident_tui, terminal_wrapped,
  one_shot}; ``anchor`` ∈ {pane, child, none}; ``needs_initial_prompt`` is a
  bool; ``required_env`` and ``activity_markers`` are lists of str;
  ``signals`` is a NON-EMPTY list of str; ``grace_seconds`` is an int;
  ``verify`` == "pid_gone".
- Per-harness value pins:
    mode:           codex/claude-code/opencode == "resident_tui";
                    dsh == "terminal_wrapped";
                    qwen/goose/crush/sweagent/aider == "one_shot".
    anchor:         codex == "child";
                    every OTHER registered harness == "none".
    needs_initial_prompt: dsh == True;
                    every OTHER registered harness == False.
    required_env:   sweagent == set {SWE_AGENT_CONFIG_DIR, SWE_AGENT_TOOLS_DIR,
                    SWE_AGENT_TRAJECTORY_DIR};
                    dsh == ["DEEPSEEK_API_KEY"];
                    codex == ["MINIMAX_API_KEY"];
                    qwen/goose/crush/aider/claude-code/opencode == [].
    signals:        codex/claude-code/opencode == ["SIGTERM"];
                    dsh/qwen/goose/crush/sweagent/aider ==
                    ["SIGINT", "SIGTERM", "SIGKILL"].
    grace_seconds:  resident_tui (codex/claude-code/opencode) == 3;
                    every other registered harness == 1.
- Unknown-harness behaviour: both specs raise ``UnknownHarnessError`` (a
  ``ValueError`` subclass); the message names the unknown harness. The empty
  string is also unknown.
- Fresh dict per call: both specs return FRESH dicts each call, with their
  list-typed values copied (so a caller mutating one return cannot corrupt
  the module-level table).

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
    UnknownHarnessError,
)
from harness_allocator.launchspec import (  # noqa: E402
    get_launch_spec,
    get_reset_spec,
    get_stop_spec,
)


# ── Derived roster — the contract (GOAL.md §2 "the derived roster is not
# optional"). NEVER hand-list harness names; walk the imported tuples. ────


ALL_HARNESSES = tuple(SUPPORTED_HARNESSES) + tuple(EXPERIMENTAL_HARNESSES)


# ── A. Derived-roster coverage (parametrized over ALL_HARNESSES) ──────────


@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_get_launch_spec_key_set_is_exactly_the_five_bound_keys(harness):
    """``get_launch_spec`` returns a dict whose keys are EXACTLY the five
    bound keys (TG1 contract). The roster is DERIVED from
    ``ALL_HARNESSES`` — never a hand-listed tuple of harness names."""
    spec = get_launch_spec(harness)
    expected_keys = {"mode", "needs_initial_prompt", "anchor", "required_env", "activity_markers", "launch_owner"}
    assert set(spec.keys()) == expected_keys, (
        f"launch spec for {harness!r} has keys {sorted(spec.keys())!r}, "
        f"expected exactly {sorted(expected_keys)!r}"
    )


@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_get_stop_spec_key_set_is_exactly_the_three_bound_keys(harness):
    """``get_stop_spec`` returns a dict whose keys are EXACTLY the three
    bound keys (TG2 contract). The roster is DERIVED from
    ``ALL_HARNESSES`` — never a hand-listed tuple of harness names."""
    spec = get_stop_spec(harness)
    expected_keys = {"signals", "grace_seconds", "verify"}
    assert set(spec.keys()) == expected_keys, (
        f"stop spec for {harness!r} has keys {sorted(spec.keys())!r}, "
        f"expected exactly {sorted(expected_keys)!r}"
    )


# ── B. Bound vocabularies + types (parametrized over ALL_HARNESSES) ───────


_LAUNCH_MODE_VOCAB = {"resident_tui", "terminal_wrapped", "one_shot"}
_LAUNCH_ANCHOR_VOCAB = {"pane", "child", "none"}


@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_launch_mode_is_in_bound_vocabulary(harness):
    """``mode`` is one of ``resident_tui``, ``terminal_wrapped``, ``one_shot``
    (GOAL.md §1 D1 / §2 binding constraints)."""
    spec = get_launch_spec(harness)
    assert spec["mode"] in _LAUNCH_MODE_VOCAB, (
        f"launch spec for {harness!r} has mode {spec['mode']!r}, "
        f"expected one of {sorted(_LAUNCH_MODE_VOCAB)}"
    )


@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_launch_anchor_is_in_bound_vocabulary(harness):
    """``anchor`` is one of ``pane``, ``child``, ``none`` (GOAL.md §1 D1 /
    §2 binding constraints)."""
    spec = get_launch_spec(harness)
    assert spec["anchor"] in _LAUNCH_ANCHOR_VOCAB, (
        f"launch spec for {harness!r} has anchor {spec['anchor']!r}, "
        f"expected one of {sorted(_LAUNCH_ANCHOR_VOCAB)}"
    )


@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_launch_needs_initial_prompt_is_bool(harness):
    """``needs_initial_prompt`` is a bool (GOAL.md §2 — "where a value's
    type is bound, bind it"). A criterion that only checks truthiness would
    pass on the int ``1``; this asserts the type explicitly."""
    spec = get_launch_spec(harness)
    assert isinstance(spec["needs_initial_prompt"], bool), (
        f"launch spec for {harness!r} has needs_initial_prompt "
        f"of type {type(spec['needs_initial_prompt']).__name__}, expected bool"
    )


@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_launch_required_env_is_list_of_str(harness):
    """``required_env`` is a list of str (env var NAMES — the names, never
    the values)."""
    spec = get_launch_spec(harness)
    required_env = spec["required_env"]
    assert isinstance(required_env, list), (
        f"launch spec for {harness!r} has required_env "
        f"of type {type(required_env).__name__}, expected list"
    )
    for name in required_env:
        assert isinstance(name, str), (
            f"launch spec for {harness!r} has required_env element "
            f"{name!r} of type {type(name).__name__}, expected str"
        )


@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_launch_activity_markers_is_list_of_str(harness):
    """``activity_markers`` is a list of str (pane strings)."""
    spec = get_launch_spec(harness)
    markers = spec["activity_markers"]
    assert isinstance(markers, list), (
        f"launch spec for {harness!r} has activity_markers "
        f"of type {type(markers).__name__}, expected list"
    )
    for marker in markers:
        assert isinstance(marker, str), (
            f"launch spec for {harness!r} has activity_markers element "
            f"{marker!r} of type {type(marker).__name__}, expected str"
        )


@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_stop_signals_is_non_empty_list_of_str(harness):
    """``signals`` is a NON-EMPTY list of str (the ordered stop ladder).
    TG2 contract."""
    spec = get_stop_spec(harness)
    signals = spec["signals"]
    assert isinstance(signals, list), (
        f"stop spec for {harness!r} has signals "
        f"of type {type(signals).__name__}, expected list"
    )
    assert signals, (
        f"stop spec for {harness!r} has empty signals ladder"
    )
    for sig in signals:
        assert isinstance(sig, str), (
            f"stop spec for {harness!r} has signals element "
            f"{sig!r} of type {type(sig).__name__}, expected str"
        )


@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_stop_grace_seconds_is_int(harness):
    """``grace_seconds`` is an int (GOAL.md §2 — "where a value's type is
    bound, bind it". TG2: a criterion that only checks truthiness passes on
    the string ``"30"``; this asserts the type explicitly)."""
    spec = get_stop_spec(harness)
    grace = spec["grace_seconds"]
    assert isinstance(grace, int), (
        f"stop spec for {harness!r} has grace_seconds "
        f"of type {type(grace).__name__}, expected int"
    )


@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_stop_verify_equals_pid_gone(harness):
    """``verify`` is bound to ``"pid_gone"`` for every registered harness
    (GOAL.md §1 D2)."""
    spec = get_stop_spec(harness)
    assert spec["verify"] == "pid_gone", (
        f"stop spec for {harness!r} has verify {spec['verify']!r}, "
        f"expected 'pid_gone'"
    )


# ── C. Per-harness value pins (name the specific harness) ────────────────


@pytest.mark.parametrize(
    "harness, expected_mode",
    [
        ("codex", "resident_tui"),
        ("claude-code", "resident_tui"),
        ("opencode", "resident_tui"),
        ("dsh", "terminal_wrapped"),
        ("qwen", "one_shot"),
        ("goose", "one_shot"),
        ("crush", "one_shot"),
        ("sweagent", "one_shot"),
        ("aider", "one_shot"),
    ],
)
def test_launch_mode_per_harness_pin(harness, expected_mode):
    """``mode`` is bound per-harness (handoff 133 D1 / D3 honest-values)."""
    spec = get_launch_spec(harness)
    assert spec["mode"] == expected_mode, (
        f"launch spec for {harness!r} has mode {spec['mode']!r}, "
        f"expected {expected_mode!r}"
    )


@pytest.mark.parametrize(
    "harness, expected_anchor",
    [
        ("codex", "child"),
        ("claude-code", "none"),
        ("opencode", "none"),
        ("dsh", "none"),
        ("qwen", "none"),
        ("goose", "none"),
        ("crush", "none"),
        ("sweagent", "none"),
        ("aider", "none"),
    ],
)
def test_launch_anchor_per_harness_pin(harness, expected_anchor):
    """``anchor`` is bound per-harness (handoff 133 D1 / D3 honest-values).
    Only codex records the harness CHILD pid; every other harness is "none"
    (one-shots have no persistent process; claude-code / opencode are
    model-allocator launched; dsh is deliberately unrecorded)."""
    spec = get_launch_spec(harness)
    assert spec["anchor"] == expected_anchor, (
        f"launch spec for {harness!r} has anchor {spec['anchor']!r}, "
        f"expected {expected_anchor!r}"
    )


@pytest.mark.parametrize(
    "harness, expected",
    [
        ("codex", False),
        ("claude-code", False),
        ("opencode", False),
        ("dsh", True),
        ("qwen", False),
        ("goose", False),
        ("crush", False),
        ("sweagent", False),
        ("aider", False),
    ],
)
def test_launch_needs_initial_prompt_per_harness_pin(harness, expected):
    """``needs_initial_prompt`` is bound per-harness (handoff 133 D1 / D3).
    Only dsh requires a cold-start wakeup after the launch command (the
    Harness Terminal wraps the one-shot dsh)."""
    spec = get_launch_spec(harness)
    assert spec["needs_initial_prompt"] is expected, (
        f"launch spec for {harness!r} has needs_initial_prompt "
        f"{spec['needs_initial_prompt']!r}, expected {expected!r}"
    )


def test_launch_required_env_sweagent_names_three_swe_agent_dirs():
    """``required_env`` for sweagent names EXACTLY the three SWE_AGENT_*_DIR
    keys (order-independent — assert SET equality). The motivating case for
    Run 036: the bare SWE-agent CLI asserts on ``CONFIG_DIR.is_dir()`` so
    these three names are LOAD-BEARING."""
    spec = get_launch_spec("sweagent")
    expected = {"SWE_AGENT_CONFIG_DIR", "SWE_AGENT_TOOLS_DIR", "SWE_AGENT_TRAJECTORY_DIR"}
    assert set(spec["required_env"]) == expected, (
        f"required_env for sweagent is {sorted(spec['required_env'])}, "
        f"expected exactly {sorted(expected)}"
    )


@pytest.mark.parametrize(
    "harness, expected",
    [
        ("codex", ["MINIMAX_API_KEY"]),
        ("dsh", ["DEEPSEEK_API_KEY"]),
        ("claude-code", []),
        ("opencode", []),
        ("qwen", []),
        ("goose", []),
        ("crush", []),
        ("aider", []),
    ],
)
def test_launch_required_env_per_harness_pin(harness, expected):
    """``required_env`` for every harness EXCEPT sweagent is order-sensitive
    (it has length 0 or 1). sweagent's case is the SET-equality test above
    (the three SWE_AGENT_*_DIR names are order-independent by contract)."""
    spec = get_launch_spec(harness)
    assert spec["required_env"] == expected, (
        f"required_env for {harness!r} is {spec['required_env']!r}, "
        f"expected {expected!r}"
    )


@pytest.mark.parametrize(
    "harness, expected",
    [
        ("codex", ["SIGTERM"]),
        ("claude-code", ["SIGTERM"]),
        ("opencode", ["SIGTERM"]),
        ("dsh", ["SIGINT", "SIGTERM", "SIGKILL"]),
        ("qwen", ["SIGINT", "SIGTERM", "SIGKILL"]),
        ("goose", ["SIGINT", "SIGTERM", "SIGKILL"]),
        ("crush", ["SIGINT", "SIGTERM", "SIGKILL"]),
        ("sweagent", ["SIGINT", "SIGTERM", "SIGKILL"]),
        ("aider", ["SIGINT", "SIGTERM", "SIGKILL"]),
    ],
)
def test_stop_signals_per_harness_pin(harness, expected):
    """``signals`` is bound per-harness (handoff 134 D2 / D3). The ladder
    is keyed on LaunchSpec.mode: resident_tui → SIGTERM-only (no SIGKILL
    escalation in ``runtime_owner._default_kill``); terminal_wrapped +
    one_shot → SIGINT →SIGTERM →SIGKILL (the ``invoke.py`` cancel ladder).
    GOAL.md §1 D3 prose claims the ladder is uniform
    ``["SIGTERM", "SIGKILL"]``; the source shows it is NOT — see
    handoff 134's recorded discrepancy."""
    spec = get_stop_spec(harness)
    assert spec["signals"] == expected, (
        f"signals for {harness!r} is {spec['signals']!r}, "
        f"expected {expected!r}"
    )


def _harnesses_with_launch_mode(mode_value):
    """DERIVED roster — harnesses whose LaunchSpec.mode equals ``mode_value``.

    The roster is derived from the spec itself, not from a hand-listed
    harness name tuple. If the next run adds a ninth harness and registers
    it in ``SUPPORTED_HARNESSES`` with ``mode == "resident_tui"``, this
    function picks it up automatically — the grace_seconds pin still holds.
    """
    return tuple(h for h in ALL_HARNESSES if get_launch_spec(h)["mode"] == mode_value)


_RESIDENT_TUI_HARNESSES = _harnesses_with_launch_mode("resident_tui")
_NON_RESIDENT_HARNESSES = tuple(
    h for h in ALL_HARNESSES if h not in _RESIDENT_TUI_HARNESSES
)


@pytest.mark.parametrize("harness", _RESIDENT_TUI_HARNESSES)
def test_stop_grace_seconds_resident_tui_is_3(harness):
    """``grace_seconds`` for every ``resident_tui`` harness is 3 (from
    ``runtime_owner._KILL_VERIFY_BOUND_SECONDS = 3.0``, line 43)."""
    spec = get_stop_spec(harness)
    assert spec["grace_seconds"] == 3, (
        f"grace_seconds for resident_tui {harness!r} is {spec['grace_seconds']!r}, "
        f"expected 3"
    )


@pytest.mark.parametrize("harness", _NON_RESIDENT_HARNESSES)
def test_stop_grace_seconds_non_resident_is_1(harness):
    """``grace_seconds`` for every NON-resident_tui harness is 1 (from
    ``invoke.CANCEL_GRACE_SECONDS = 1.0``, line 57). Covers terminal_wrapped
    (dsh) and one_shot (qwen, goose, crush, sweagent, aider)."""
    spec = get_stop_spec(harness)
    assert spec["grace_seconds"] == 1, (
        f"grace_seconds for non-resident_tui {harness!r} is {spec['grace_seconds']!r}, "
        f"expected 1"
    )


# ── D. Unknown-harness behaviour ─────────────────────────────────────────


def test_get_launch_spec_unknown_harness_raises_unknown_harness_error_naming_it():
    """``get_launch_spec("bogus")`` raises ``UnknownHarnessError``; the
    message names the unknown harness (mirrors
    ``capabilities.get_capabilities``)."""
    with pytest.raises(UnknownHarnessError) as excinfo:
        get_launch_spec("bogus")
    assert "bogus" in str(excinfo.value), (
        f"UnknownHarnessError message {str(excinfo.value)!r} does not name "
        f"the unknown harness 'bogus'"
    )


def test_get_stop_spec_unknown_harness_raises_unknown_harness_error_naming_it():
    """``get_stop_spec("bogus")`` raises ``UnknownHarnessError``; the message
    names the unknown harness."""
    with pytest.raises(UnknownHarnessError) as excinfo:
        get_stop_spec("bogus")
    assert "bogus" in str(excinfo.value), (
        f"UnknownHarnessError message {str(excinfo.value)!r} does not name "
        f"the unknown harness 'bogus'"
    )


@pytest.mark.parametrize(
    "fn",
    [get_launch_spec, get_stop_spec],
    ids=["get_launch_spec", "get_stop_spec"],
)
def test_both_specs_raise_unknown_harness_error_for_empty_string(fn):
    """Both specs raise ``UnknownHarnessError`` for the empty string
    (a sentinel that would otherwise slip past many "is it a registered
    name?" checks)."""
    with pytest.raises(UnknownHarnessError) as excinfo:
        fn("")
    # The message should be present (the module is responsible for naming
    # the unknown harness, even if the repr of "" is the empty string).
    assert excinfo.value is not None


def test_unknown_harness_error_is_value_error_subclass():
    """``UnknownHarnessError`` IS a ``ValueError`` subclass (a typed error
    callers can catch generically)."""
    assert issubclass(UnknownHarnessError, ValueError), (
        f"UnknownHarnessError (MRO: {UnknownHarnessError.__mro__!r}) "
        f"is not a ValueError subclass"
    )


# ── E. Fresh dict per call / no shared mutable state ────────────────────


@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_get_launch_spec_returns_fresh_dict_per_call(harness):
    """Two calls to ``get_launch_spec(harness)`` return dicts that are not
    the same object, and their list-typed values (``required_env``,
    ``activity_markers``) are not the same object either. A caller mutating
    one return cannot corrupt the module-level table or a later call."""
    a = get_launch_spec(harness)
    b = get_launch_spec(harness)
    assert a is not b, (
        f"two calls to get_launch_spec({harness!r}) returned the same dict object"
    )
    assert a["required_env"] is not b["required_env"], (
        f"required_env lists from two calls to get_launch_spec({harness!r}) "
        f"are the same object — mutating one would corrupt the other"
    )
    assert a["activity_markers"] is not b["activity_markers"], (
        f"activity_markers lists from two calls to get_launch_spec({harness!r}) "
        f"are the same object — mutating one would corrupt the other"
    )
    # Demonstrate the no-shared-state contract concretely:
    original = list(a["required_env"])
    a["required_env"].append("MUTATION_SENTINEL")
    assert b["required_env"] == original, (
        f"mutating a['required_env'] corrupted b['required_env'] for "
        f"harness {harness!r}"
    )
    # And a fresh call sees no corruption either.
    c = get_launch_spec(harness)
    assert "MUTATION_SENTINEL" not in c["required_env"], (
        f"a subsequent call to get_launch_spec({harness!r}) sees the mutation "
        f"sentinel — module-level table was corrupted"
    )


@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_get_stop_spec_returns_fresh_dict_per_call(harness):
    """Two calls to ``get_stop_spec(harness)`` return dicts that are not
    the same object, and their ``signals`` lists are not the same object
    either. A caller mutating one return cannot corrupt the module-level
    table or a later call."""
    a = get_stop_spec(harness)
    b = get_stop_spec(harness)
    assert a is not b, (
        f"two calls to get_stop_spec({harness!r}) returned the same dict object"
    )
    assert a["signals"] is not b["signals"], (
        f"signals lists from two calls to get_stop_spec({harness!r}) "
        f"are the same object — mutating one would corrupt the other"
    )
    # Demonstrate the no-shared-state contract concretely:
    original = list(a["signals"])
    a["signals"].append("MUTATION_SENTINEL")
    assert b["signals"] == original, (
        f"mutating a['signals'] corrupted b['signals'] for "
        f"harness {harness!r}"
    )
    c = get_stop_spec(harness)
    assert "MUTATION_SENTINEL" not in c["signals"], (
        f"a subsequent call to get_stop_spec({harness!r}) sees the mutation "
        f"sentinel — module-level table was corrupted"
    )


# ── ResetSpec (2026-08-30, alignment item 15) ────────────────────────────


@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_get_reset_spec_covers_the_whole_roster(harness):
    """Every registered harness has a ResetSpec — fail-closed derivation."""
    spec = get_reset_spec(harness)
    assert set(spec.keys()) == {"method", "command"}, (
        f"ResetSpec for {harness!r} must have EXACTLY method+command, "
        f"got {sorted(spec.keys())!r}"
    )
    assert spec["method"] in ("slash_command", "restart"), (
        f"ResetSpec method for {harness!r} outside vocabulary: "
        f"{spec['method']!r}"
    )
    if spec["method"] == "slash_command":
        assert isinstance(spec["command"], str) and spec["command"].startswith("/"), (
            f"slash_command reset for {harness!r} must carry a /command, "
            f"got {spec['command']!r}"
        )
    else:
        assert spec["command"] is None, (
            f"restart reset for {harness!r} must not carry a command, "
            f"got {spec['command']!r}"
        )


def test_reset_spec_pins_the_known_in_session_resets():
    """The two resident TUIs with in-session resets are pinned literally.

    claude-code clears with /clear, opencode with /new (the values DPMtF's
    1010 roles carry in fresh_session_command — measured live behaviour,
    now declared). codex is restart: its context release has always been
    stop+relaunch (codex_context_release), never a slash command.
    """
    assert get_reset_spec("claude-code") == {
        "method": "slash_command", "command": "/clear"}
    assert get_reset_spec("opencode") == {
        "method": "slash_command", "command": "/new"}
    assert get_reset_spec("codex") == {"method": "restart", "command": None}


def test_reset_spec_one_shots_are_restart():
    """Every one_shot harness resets by restart: a fresh invocation IS a
    fresh context (simple-harness has no in-session reset at all —
    sessions.go is read-only, no resume)."""
    for harness in ALL_HARNESSES:
        if get_launch_spec(harness)["mode"] == "one_shot":
            assert get_reset_spec(harness)["method"] == "restart", (
                f"one_shot harness {harness!r} declared a non-restart reset"
            )


def test_get_reset_spec_unknown_harness_raises():
    with pytest.raises(UnknownHarnessError) as exc_info:
        get_reset_spec("no-such-harness")
    assert "no-such-harness" in str(exc_info.value)


@pytest.mark.parametrize("harness", ALL_HARNESSES)
def test_get_reset_spec_returns_fresh_dict_per_call(harness):
    a = get_reset_spec(harness)
    b = get_reset_spec(harness)
    assert a is not b
    a["command"] = "MUTATION_SENTINEL"
    assert get_reset_spec(harness)["command"] != "MUTATION_SENTINEL"
