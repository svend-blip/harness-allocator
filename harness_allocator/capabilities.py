"""Normalized capability manifest for the five supported harnesses.

The manifest is a dict with EXACTLY five groups (the ROADMAP §3 normalized
shape MINUS its speculative ``models`` and ``workflow`` groups, which this run
deliberately drops — GOAL.md Run 020 §1 D1). The fields in each group are
listed below; no field or group is added beyond these.

    execution:    terminal (bool), headless (bool), interactive (bool)
    workspace:    read_only (bool), workspace_write (bool), full_access (bool)
    sessions:     persistent_session (bool), session_resume (bool), mode (str)
    extensions:   skills (bool), mcp (bool), custom_tools (bool)
    automation:   non_interactive (bool), deterministic_exit (bool),
                  interrupt_safe (bool)

Values are grounded in measured behavior of the existing package
(``harness_allocator/adapter.py``, ``harness_allocator/invoke.py``,
``harness_allocator/definition.py``, ``harness_allocator/terminal.py``):

- codex: resident TUI launched by ``adapter._codex_argv``
  (``codex -m <model> -C ... --sandbox ... --ask-for-approval ...``); one-shot
  form rejected by ``adapter.build_task_argv`` ("no one-shot task invocation
  for harness 'codex'"); MCP via ``adapter.build_codex_mcp_setup_argv``
  (``codex mcp add <name> --url <url>``).
- claude-code / opencode: resident interactive TUI coding clients (the package
  does NOT build their launch argv — they are DPMtF-launched per
  ``terminal.HARNESS_LABELS``); manifest shape identical to codex except
  ``sessions.mode = "resident"``.
- dsh: headless one-shot invoked by ``adapter.build_dsh_argv``
  (``dsh --profile <p> [--patch <p>] [task]``); MCP via
  ``adapter.build_dsh_mcp_patch_yml`` (DSH MCP patch overlay); the measured
  SIGINT → SIGTERM → SIGKILL process-group cancel path lives in
  ``invoke.run_argv`` (CANCEL_GRACE_SECONDS = 1.0,
  _CANCEL_TERM_ESCALATION_FRACTION = 0.5).

``sessions.mode`` values are BOUND (non-negotiable):
    codex        → "fresh"
    claude-code  → "resident"
    opencode     → "resident"
    dsh          → "oneshot"

The four typed errors are :class:`ValueError` subclasses (the package's idiom —
legacy ``except ValueError`` callers keep working). They are bound by name so
later handoffs (``runspec.py`` RunSpec validation, ``invoke.execute_spec``
capability refusal) can import them from here:

    UnknownHarnessError        — raised by ``get_capabilities`` for any harness
                                 key not in ('codex', 'claude-code', 'opencode',
                                 'dsh', 'qwen'). USED IN THIS HANDOFF.
    MissingPromptError         — reserved for RunSpec validation (handoff 2).
    UnsupportedCapabilityError — reserved for execute_spec capability refusal
                                 (handoff 3).
    LargeInputRefusedError     — reserved for execute_spec large-input refusal
                                 (handoff 3).

This module is stdlib-only (no new imports).
"""

from __future__ import annotations


# ── Typed errors (ValueError subclasses — package idiom) ───────────────


class UnknownHarnessError(ValueError):
    """Raised by ``get_capabilities`` for a harness key that is not one of
    the four supported harnesses (``codex``, ``claude-code``, ``opencode``,
    ``dsh``). The message names the unknown harness.
    """


class MissingPromptError(ValueError):
    """Reserved for RunSpec validation (handoff 2 of Run 020). Defined here
    so the bound name is importable from a single place.
    """


class UnsupportedCapabilityError(ValueError):
    """Reserved for execute_spec capability refusal (handoff 3 of Run 020).
    Defined here so the bound name is importable from a single place.
    """


class LargeInputRefusedError(ValueError):
    """Reserved for execute_spec large-input refusal (handoff 3 of Run 020).
    Defined here so the bound name is importable from a single place.
    """


# ── Per-harness manifests ───────────────────────────────────────────────
#
# These dicts are LITERAL — values are not computed at runtime. They are the
# source of truth for the manifest. Adding fields or groups beyond those
# listed in this module's docstring is a contract violation.
#
# Sources of truth per harness are stated inline above each dict.

# codex — resident TUI (adapter._codex_argv); one-shot form rejected by
# adapter.build_task_argv; MCP via adapter.build_codex_mcp_setup_argv.
_MANIFEST_CODEX = {
    "execution": {
        "terminal": True,
        "headless": False,
        "interactive": True,
    },
    "workspace": {
        "read_only": True,
        "workspace_write": True,
        "full_access": False,
    },
    "sessions": {
        "persistent_session": True,
        "session_resume": True,
        "mode": "fresh",
    },
    "extensions": {
        "skills": True,
        "mcp": True,
        "custom_tools": False,
    },
    "automation": {
        "non_interactive": False,
        "deterministic_exit": False,
        "interrupt_safe": False,
    },
}

# claude-code — resident interactive TUI coding client (DPMtF-launched per
# terminal.HARNESS_LABELS). Identical shape to codex except sessions.mode.
_MANIFEST_CLAUDE_CODE = {
    "execution": {
        "terminal": True,
        "headless": False,
        "interactive": True,
    },
    "workspace": {
        "read_only": True,
        "workspace_write": True,
        "full_access": False,
    },
    "sessions": {
        "persistent_session": True,
        "session_resume": True,
        "mode": "resident",
    },
    "extensions": {
        "skills": True,
        "mcp": True,
        "custom_tools": False,
    },
    "automation": {
        "non_interactive": False,
        "deterministic_exit": False,
        "interrupt_safe": False,
    },
}

# opencode — resident interactive TUI coding client (DPMtF-launched per
# terminal.HARNESS_LABELS). Identical shape to codex except sessions.mode.
_MANIFEST_OPENCODE = {
    "execution": {
        "terminal": True,
        "headless": False,
        "interactive": True,
    },
    "workspace": {
        "read_only": True,
        "workspace_write": True,
        "full_access": False,
    },
    "sessions": {
        "persistent_session": True,
        "session_resume": True,
        "mode": "resident",
    },
    "extensions": {
        "skills": True,
        "mcp": True,
        "custom_tools": False,
    },
    "automation": {
        "non_interactive": False,
        "deterministic_exit": False,
        "interrupt_safe": False,
    },
}

# dsh — headless one-shot (adapter.build_dsh_argv); MCP via
# adapter.build_dsh_mcp_patch_yml; measured cancel path
# (SIGINT → SIGTERM → SIGKILL) in invoke.run_argv.
_MANIFEST_DSH = {
    "execution": {
        "terminal": False,
        "headless": True,
        "interactive": False,
    },
    "workspace": {
        "read_only": False,
        "workspace_write": True,
        "full_access": False,
    },
    "sessions": {
        "persistent_session": False,
        "session_resume": False,
        "mode": "oneshot",
    },
    "extensions": {
        "skills": False,
        "mcp": True,
        "custom_tools": False,
    },
    "automation": {
        "non_interactive": True,
        "deterministic_exit": True,
        "interrupt_safe": True,
    },
}

# qwen — headless one-shot invoked by adapter.build_qwen_argv
# ([qwen, --approval-mode, <mode>, --include-directories <dir>*, -p, <task>]);
# model/endpoint live in the child env (adapter.build_qwen_env), not argv;
# measured cancel path (SIGINT → SIGTERM → SIGKILL) in invoke.run_argv.
# Grounded in the installed qwen CLI 0.22.0 (`qwen --help`) and the headless
# one-shot shape of the dsh manifest: qwen -p is a headless single-shot
# invocation (Qwen Code's own help: "use -p/--prompt for non-interactive
# mode"); sessions resume (-c/--continue, -r/--resume) is supported in the
# CLI but NOT in -p mode (one-shot here); qwen has both `qwen mcp`
# (extensions.mcp) and /skills in interactive mode (extensions.skills);
# automation.deterministic_exit is the bound D1 fact for -p; automation.
# interrupt_safe mirrors dsh — invoke.run_argv's measured cancel path.
_MANIFEST_QWEN = {
    "execution": {
        "terminal": False,
        "headless": True,
        "interactive": False,
    },
    "workspace": {
        "read_only": False,
        "workspace_write": True,
        "full_access": False,
    },
    "sessions": {
        "persistent_session": False,
        "session_resume": True,
        "mode": "oneshot",
    },
    "extensions": {
        "skills": True,
        "mcp": True,
        "custom_tools": False,
    },
    "automation": {
        "non_interactive": True,
        "deterministic_exit": True,
        "interrupt_safe": True,
    },
}

# ── Public API ─────────────────────────────────────────────────────────


#: The set of harness keys that ``get_capabilities`` accepts.
SUPPORTED_HARNESSES = ("codex", "claude-code", "opencode", "dsh", "qwen")


def get_capabilities(harness) -> dict:
    """Return the normalized capability manifest for ``harness``.

    The returned dict has EXACTLY five groups (``execution``, ``workspace``,
    ``sessions``, ``extensions``, ``automation``) with the fields documented
    in this module's docstring; no field or group is added beyond those.

    Raises :class:`UnknownHarnessError` (a :class:`ValueError` subclass) when
    ``harness`` is not one of ``codex``, ``claude-code``, ``opencode``,
    ``dsh``. The error message names the unknown harness.
    """
    if harness == "codex":
        # Return a fresh copy so callers cannot mutate the module-level dict.
        return {
            "execution": dict(_MANIFEST_CODEX["execution"]),
            "workspace": dict(_MANIFEST_CODEX["workspace"]),
            "sessions": dict(_MANIFEST_CODEX["sessions"]),
            "extensions": dict(_MANIFEST_CODEX["extensions"]),
            "automation": dict(_MANIFEST_CODEX["automation"]),
        }
    if harness == "claude-code":
        return {
            "execution": dict(_MANIFEST_CLAUDE_CODE["execution"]),
            "workspace": dict(_MANIFEST_CLAUDE_CODE["workspace"]),
            "sessions": dict(_MANIFEST_CLAUDE_CODE["sessions"]),
            "extensions": dict(_MANIFEST_CLAUDE_CODE["extensions"]),
            "automation": dict(_MANIFEST_CLAUDE_CODE["automation"]),
        }
    if harness == "opencode":
        return {
            "execution": dict(_MANIFEST_OPENCODE["execution"]),
            "workspace": dict(_MANIFEST_OPENCODE["workspace"]),
            "sessions": dict(_MANIFEST_OPENCODE["sessions"]),
            "extensions": dict(_MANIFEST_OPENCODE["extensions"]),
            "automation": dict(_MANIFEST_OPENCODE["automation"]),
        }
    if harness == "dsh":
        return {
            "execution": dict(_MANIFEST_DSH["execution"]),
            "workspace": dict(_MANIFEST_DSH["workspace"]),
            "sessions": dict(_MANIFEST_DSH["sessions"]),
            "extensions": dict(_MANIFEST_DSH["extensions"]),
            "automation": dict(_MANIFEST_DSH["automation"]),
        }
    if harness == "qwen":
        return {
            "execution": dict(_MANIFEST_QWEN["execution"]),
            "workspace": dict(_MANIFEST_QWEN["workspace"]),
            "sessions": dict(_MANIFEST_QWEN["sessions"]),
            "extensions": dict(_MANIFEST_QWEN["extensions"]),
            "automation": dict(_MANIFEST_QWEN["automation"]),
        }
    raise UnknownHarnessError(f"unknown harness: {harness!r}")
