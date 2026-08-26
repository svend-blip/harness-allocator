"""LaunchSpec and StopSpec — the harness declares how it starts and how it stops.

Per Run 036 / D1 + D2 (GOAL.md §1, §2 binding constraints). This module is the
allocator-side declaration surface for how each registered harness comes up
and how it is taken down. ``get_launch_spec`` (handoff 133 / D1) and
``get_stop_spec`` (handoff 134 / D2) live side-by-side in the same module so
the roster rule and the table shape are kept in one place.

Roster rule
-----------
The roster is DERIVED, never hand-listed: the module imports
``SUPPORTED_HARNESSES`` and ``EXPERIMENTAL_HARNESSES`` from
``harness_allocator.capabilities`` and answers for every harness in both
tuples. A hand-listed roster would stop covering the next adapter someone
registers — exactly when a universal contract matters (GOAL.md §2:
"the derived roster is not optional").

Bound keys (GOAL.md §1 / §2 — names bound)
------------------------------------------
The LaunchSpec dict has EXACTLY five keys:

  - ``mode``               one of ``resident_tui`` | ``terminal_wrapped`` | ``one_shot``
  - ``needs_initial_prompt`` (bool) — cold-start first wakeup after the launch command
  - ``anchor``             one of ``pane`` | ``child`` | ``none`` — which pid to record
  - ``required_env``       list of env var NAMES the harness cannot run without
  - ``activity_markers``   list of pane strings that mean "this harness is working"

Where each value was grounded
-----------------------------
The per-harness values were VERIFIED against the sources named below before
binding. If a source disagrees with a bound value, the SOURCE wins and the
result section records the disagreement (GOAL.md §2: "Declare, do not
change" — the spec is wrong until proven otherwise).

  - ``mode`` for codex, claude-code, opencode, qwen, goose, crush, sweagent,
    aider — derived from the adapter's argv shape (``adapter.build_launch_command``
    / ``build_task_invocation``) and the DPMtF branch in
    ``start_coding.py``: resident TUI clients run interactively (codex,
    claude-code, opencode); one-shots return on their own (qwen, goose, crush,
    sweagent, aider). ``mode`` for dsh = ``"terminal_wrapped"`` —
    ``start_coding.py`` (lines 522–546) launches the persistent Harness
    Terminal that wraps the one-shot dsh; the dsh process is NOT persistent.

  - ``needs_initial_prompt`` — True for dsh (start_coding.py lines 546–555
    sends the cold-start supervisor prompt to the terminal after launching
    it); False for every other registered harness.

  - ``anchor`` — "child" for codex (start_coding.py
    ``_record_harness_ownership`` records the harness CHILD pid via
    ``_harness_child_pid``; the pane-shell pid is deliberately NOT recorded
    because it is TERM-immune — Run 031 incident 2026-08-21). "none" for
    dsh (deliberately unrecorded per start_coding.py lines 515–521: "Do NOT
    register the (absent) dsh process as a persistent harness_process") and
    for every other harness (one-shots have no persistent process;
    claude-code / opencode are model-allocator launched with no ownership
    record).

  - ``required_env`` — "DEEPSEEK_API_KEY" for dsh and "MINIMAX_API_KEY" for
    codex from ``definition.REQUIRED_ENV`` (the canonical "harness cannot
    run without" table). The three SWE_AGENT_*_DIR names for sweagent from
    ``adapter.build_sweagent_env`` (lines 466–507) — these are LOAD-BEARING
    for the installed v1.1.0 git checkout (the bare ``sweagent --version``
    asserts on ``CONFIG_DIR.is_dir()``). Empty list for every other harness
    (qwen / goose / crush / aider read credentials inherited from the parent
    env; claude-code / opencode are model-allocator launched).

  - ``activity_markers`` — the same three strings for every registered
    harness, verbatim from ``chain_watchdog.ACTIVITY_MARKERS`` (line 120).
    Today the watchdog applies this ONE hardcoded tuple to every harness;
    nobody differentiated it, so the HONEST declaration is the same tuple
    per-harness. GOAL.md §7 keeps chain_watchdog's hardcoded markers this
    run (improving the per-harness marker set is a non-goal).

Unknown harness
---------------
``get_launch_spec`` raises the package's typed exception —
``capabilities.UnknownHarnessError``, a :class:`ValueError` subclass — naming
the unknown harness. This mirrors the existing ``get_capabilities`` idiom so
callers can catch a single exception class for either surface.

Caller-mutation safety
-----------------------
``get_launch_spec`` returns a FRESH dict per call (deep-copied nested lists)
so a caller mutating the result cannot corrupt the module-level table. This
mirrors ``get_capabilities``.
"""

from .capabilities import (
    EXPERIMENTAL_HARNESSES,
    SUPPORTED_HARNESSES,
    UnknownHarnessError,
)


#: Pane strings that mean "this harness is working". Today this is the ONE
#: hardcoded tuple ``chain_watchdog.ACTIVITY_MARKERS`` (line 120) applied to
#: every harness — nobody differentiated it. The honest declaration is the
#: same tuple per-harness. GOAL.md §7 keeps chain_watchdog's hardcoded
#: markers this run; per-harness differentiation is a non-goal.
_ACTIVITY_MARKERS = ("esc interrupt", "esc to interrupt", "↓")


#: LaunchSpec table for the SEVEN supported harnesses (literal per-harness
#: values; not computed at runtime). Values are NOT computed at import time —
#: each is grounded in the source named in this module's docstring.
_LAUNCH_SPEC_SUPPORTED = {
    "codex": {
        "mode": "resident_tui",
        "needs_initial_prompt": False,
        "anchor": "child",
        "required_env": ["MINIMAX_API_KEY"],
        "activity_markers": list(_ACTIVITY_MARKERS),
    },
    "claude-code": {
        "mode": "resident_tui",
        "needs_initial_prompt": False,
        "anchor": "none",
        "required_env": [],
        "activity_markers": list(_ACTIVITY_MARKERS),
    },
    "opencode": {
        "mode": "resident_tui",
        "needs_initial_prompt": False,
        "anchor": "none",
        "required_env": [],
        "activity_markers": list(_ACTIVITY_MARKERS),
    },
    "dsh": {
        "mode": "terminal_wrapped",
        "needs_initial_prompt": True,
        "anchor": "none",
        "required_env": ["DEEPSEEK_API_KEY"],
        "activity_markers": list(_ACTIVITY_MARKERS),
    },
    "qwen": {
        "mode": "one_shot",
        "needs_initial_prompt": False,
        "anchor": "none",
        "required_env": [],
        "activity_markers": list(_ACTIVITY_MARKERS),
    },
    "goose": {
        "mode": "one_shot",
        "needs_initial_prompt": False,
        "anchor": "none",
        "required_env": [],
        "activity_markers": list(_ACTIVITY_MARKERS),
    },
    "crush": {
        "mode": "one_shot",
        "needs_initial_prompt": False,
        "anchor": "none",
        "required_env": [],
        "activity_markers": list(_ACTIVITY_MARKERS),
    },
}


#: LaunchSpec table for the TWO experimental harnesses (sweagent, aider).
#: Same key set; values grounded in adapter.py / chain_watchdog.py.
_LAUNCH_SPEC_EXPERIMENTAL = {
    "sweagent": {
        "mode": "one_shot",
        "needs_initial_prompt": False,
        "anchor": "none",
        "required_env": [
            "SWE_AGENT_CONFIG_DIR",
            "SWE_AGENT_TOOLS_DIR",
            "SWE_AGENT_TRAJECTORY_DIR",
        ],
        "activity_markers": list(_ACTIVITY_MARKERS),
    },
    "aider": {
        "mode": "one_shot",
        "needs_initial_prompt": False,
        "anchor": "none",
        "required_env": [],
        "activity_markers": list(_ACTIVITY_MARKERS),
    },
}


def _all_specs():
    """Yield every harness's LaunchSpec dict (roster is DERIVED).

    Concatenates the supported and experimental tables by walking the
    IMPORTS, not by enumerating the table keys in this module. If a new
    harness is added to ``SUPPORTED_HARNESSES`` or ``EXPERIMENTAL_HARNESSES``
    without a corresponding entry in this module's tables, ``get_launch_spec``
    raises :class:`UnknownHarnessError` — failing closed rather than silently
    returning a partial record.
    """
    for name in SUPPORTED_HARNESSES:
        spec = _LAUNCH_SPEC_SUPPORTED.get(name)
        if spec is None:
            raise UnknownHarnessError(f"unknown harness: {name!r}")
        yield name, spec
    for name in EXPERIMENTAL_HARNESSES:
        spec = _LAUNCH_SPEC_EXPERIMENTAL.get(name)
        if spec is None:
            raise UnknownHarnessError(f"unknown harness: {name!r}")
        yield name, spec


def get_launch_spec(harness) -> dict:
    """Return the LaunchSpec dict for ``harness``.

    The returned dict has EXACTLY five keys: ``mode``, ``needs_initial_prompt``,
    ``anchor``, ``required_env``, ``activity_markers``. Values are deep-copied
    per call so a caller mutating the result cannot corrupt the module-level
    table (mirrors :func:`harness_allocator.capabilities.get_capabilities`).

    Raises :class:`harness_allocator.capabilities.UnknownHarnessError` (a
    :class:`ValueError` subclass) when ``harness`` is not one of the names
    in ``SUPPORTED_HARNESSES`` or ``EXPERIMENTAL_HARNESSES``. The error
    message names the unknown harness.

    The roster is DERIVED from the imported tuples — never hand-listed.
    """
    for name, spec in _all_specs():
        if name == harness:
            return {
                "mode": spec["mode"],
                "needs_initial_prompt": spec["needs_initial_prompt"],
                "anchor": spec["anchor"],
                "required_env": list(spec["required_env"]),
                "activity_markers": list(spec["activity_markers"]),
            }
    raise UnknownHarnessError(f"unknown harness: {harness!r}")


# --- StopSpec surface (handoff 134 / D2) ----------------------------------
#
# StopSpec dict shape (GOAL.md §1 / §2 — names bound):
#   - signals       list of str  — ordered stop ladder
#   - grace_seconds int           — grace period for the ladder
#   - verify        str           — bound to "pid_gone" for now
#
# Where each value was grounded
# -----------------------------
# The per-harness values were VERIFIED against the sources named below before
# binding. If a source disagrees with a bound value, the SOURCE wins and the
# result section records the disagreement (GOAL.md §2: "Declare, do not
# change" — the spec is wrong until proven otherwise; D3: "honest values,
# measured"). Do NOT invent a "better" ladder — this run declares today's
# behaviour, it does not improve it (GOAL.md §7).
#
#   - ``verify`` = ``"pid_gone"`` for every harness. runtime_owner._default_kill
#     (lines 133–171) verifies the pid is gone by polling ``os.kill(pid, 0)``
#     until ``ProcessLookupError`` (the process is verifiably gone). invoke.py
#     verifies via ``proc.poll()`` / ``proc.wait()``. The shared declaration
#     is "we confirm the pid is gone before declaring stopped" — the
#     implementation detail (poll vs wait) is what an oracle consumer can
#     read but does not need to vary per-harness today.
#
#   - ``grace_seconds`` keyed on the harness's LaunchSpec mode:
#       * resident_tui (codex, claude-code, opencode) → 3 — the
#         ``runtime_owner._KILL_VERIFY_BOUND_SECONDS = 3.0`` bounded wait
#         (line 43) after SIGTERM before declaring a survivor.
#       * terminal_wrapped + one_shot (dsh, qwen, goose, crush, sweagent,
#         aider) → 1 — the ``invoke.CANCEL_GRACE_SECONDS = 1.0`` total cancel
#         grace (line 57), which is also the SIGINT→SIGTERM→SIGKILL escalation
#         envelope (lines 286 + 301–307).
#
#   - ``signals`` keyed on the harness's LaunchSpec mode:
#       * resident_tui (codex, claude-code, opencode) → ``["SIGTERM"]`` —
#         runtime_owner._default_kill sends SIGTERM at line 153 and verifies
#         exit; there is NO SIGKILL escalation in DPMtF's resident stop path
#         (a survivor past 3.0s returns False and keeps its ownership row).
#       * terminal_wrapped + one_shot (dsh, qwen, goose, crush, sweagent,
#         aider) → ``["SIGINT", "SIGTERM", "SIGKILL"]`` — invoke.py cancel
#         ladder lines 298–307: SIGINT at stage 1, SIGTERM at stage 2 after
#         ``_CANCEL_TERM_ESCALATION_FRACTION`` (= 0.5) × grace (= 0.5s),
#         SIGKILL at stage 3 after full grace (= 1.0s); a timeout also sends
#         SIGKILL directly at line 295.
#
# Recorded discrepancy against GOAL.md §1 D3 prose
# ------------------------------------------------
# GOAL.md §1 D3 states the stop ladder is uniform today and instructs
# declaring ``["SIGTERM", "SIGKILL"]``. The source shows the ladder is NOT
# uniform and matches NEITHER form exactly: the DPMtF resident stop path
# (``runtime_owner._default_kill``) is SIGTERM-only (no SIGKILL), and the
# allocator one-shot cancel path (``invoke.py``) is SIGINT→SIGTERM→SIGKILL.
# Per GOAL.md §2 ("Declare, do not change" — the spec is wrong until proven
# otherwise) and D3 ("honest values, measured"), the LIVE behaviour is
# declared above and the GOAL.md D3 prose is recorded here as a known
# discrepancy to resolve in a later run (likely as part of the runtime
# migration to read this spec).

#: StopSpec table for the SEVEN supported harnesses. Literal per-harness
#: values; not computed at runtime.
_STOP_SPEC_SUPPORTED = {
    "codex": {
        # LaunchSpec.mode = "resident_tui" → SIGTERM-only ladder, 3 s grace.
        "signals": ["SIGTERM"],
        "grace_seconds": 3,
        "verify": "pid_gone",
    },
    "claude-code": {
        # LaunchSpec.mode = "resident_tui" → SIGTERM-only ladder, 3 s grace.
        "signals": ["SIGTERM"],
        "grace_seconds": 3,
        "verify": "pid_gone",
    },
    "opencode": {
        # LaunchSpec.mode = "resident_tui" → SIGTERM-only ladder, 3 s grace.
        "signals": ["SIGTERM"],
        "grace_seconds": 3,
        "verify": "pid_gone",
    },
    "dsh": {
        # LaunchSpec.mode = "terminal_wrapped" → invoke ladder, 1 s grace.
        "signals": ["SIGINT", "SIGTERM", "SIGKILL"],
        "grace_seconds": 1,
        "verify": "pid_gone",
    },
    "qwen": {
        # LaunchSpec.mode = "one_shot" → invoke ladder, 1 s grace.
        "signals": ["SIGINT", "SIGTERM", "SIGKILL"],
        "grace_seconds": 1,
        "verify": "pid_gone",
    },
    "goose": {
        # LaunchSpec.mode = "one_shot" → invoke ladder, 1 s grace.
        "signals": ["SIGINT", "SIGTERM", "SIGKILL"],
        "grace_seconds": 1,
        "verify": "pid_gone",
    },
    "crush": {
        # LaunchSpec.mode = "one_shot" → invoke ladder, 1 s grace.
        "signals": ["SIGINT", "SIGTERM", "SIGKILL"],
        "grace_seconds": 1,
        "verify": "pid_gone",
    },
}


#: StopSpec table for the TWO experimental harnesses (sweagent, aider).
#: Same key set; values grounded in invoke.py / chain_watchdog.py.
_STOP_SPEC_EXPERIMENTAL = {
    "sweagent": {
        # LaunchSpec.mode = "one_shot" → invoke ladder, 1 s grace.
        "signals": ["SIGINT", "SIGTERM", "SIGKILL"],
        "grace_seconds": 1,
        "verify": "pid_gone",
    },
    "aider": {
        # LaunchSpec.mode = "one_shot" → invoke ladder, 1 s grace.
        "signals": ["SIGINT", "SIGTERM", "SIGKILL"],
        "grace_seconds": 1,
        "verify": "pid_gone",
    },
}


def _all_stop_specs():
    """Yield every harness's StopSpec dict (roster is DERIVED).

    Concatenates the supported and experimental tables by walking the
    IMPORTS, not by enumerating the table keys in this module. If a new
    harness is added to ``SUPPORTED_HARNESSES`` or ``EXPERIMENTAL_HARNESSES``
    without a corresponding entry in this module's tables,
    ``get_stop_spec`` raises :class:`UnknownHarnessError` — failing closed
    rather than silently returning a partial record.
    """
    for name in SUPPORTED_HARNESSES:
        spec = _STOP_SPEC_SUPPORTED.get(name)
        if spec is None:
            raise UnknownHarnessError(f"unknown harness: {name!r}")
        yield name, spec
    for name in EXPERIMENTAL_HARNESSES:
        spec = _STOP_SPEC_EXPERIMENTAL.get(name)
        if spec is None:
            raise UnknownHarnessError(f"unknown harness: {name!r}")
        yield name, spec


def get_stop_spec(harness) -> dict:
    """Return the StopSpec dict for ``harness``.

    The returned dict has EXACTLY three keys: ``signals``, ``grace_seconds``,
    ``verify``. Values are deep-copied per call (the ``signals`` list in
    particular, so a caller mutating it cannot corrupt the module-level
    table) so the return value mirrors :func:`get_launch_spec` and
    :func:`harness_allocator.capabilities.get_capabilities`.

    Raises :class:`harness_allocator.capabilities.UnknownHarnessError` (a
    :class:`ValueError` subclass) when ``harness`` is not one of the names
    in ``SUPPORTED_HARNESSES`` or ``EXPERIMENTAL_HARNESSES``. The error
    message names the unknown harness.

    The roster is DERIVED from the imported tuples — never hand-listed.

    StopSpec is a DECLARATION of today's behaviour, not an improvement of
    it. The recorded discrepancy against GOAL.md §1 D3 (the prose claims
    the ladder is uniform ``["SIGTERM", "SIGKILL"]``) is captured in the
    module-level docstring above.
    """
    for name, spec in _all_stop_specs():
        if name == harness:
            return {
                "signals": list(spec["signals"]),
                "grace_seconds": spec["grace_seconds"],
                "verify": spec["verify"],
            }
    raise UnknownHarnessError(f"unknown harness: {harness!r}")
