"""Permission enforcement contracts for the harness allocator.

Defines the three normalized permission modes, their enforcement logic against
per-harness capability manifests, and MCP trust tier assignments.

Key contract: a mode the manifest cannot enforce MUST raise PermissionRefusedError.
Never silently downgrade. Never silently broaden.
"""

from __future__ import annotations

from harness_allocator.capabilities import (
    SUPPORTED_HARNESSES,
    EXPERIMENTAL_HARNESSES,
    _MANIFEST_CODEX,
    _MANIFEST_CLAUDE_CODE,
    _MANIFEST_OPENCODE,
    _MANIFEST_DSH,
    _MANIFEST_QWEN,
    _MANIFEST_GOOSE,
    _MANIFEST_CRUSH,
    _MANIFEST_SWEAGENT,
    _MANIFEST_AIDER,
    _MANIFEST_WHIP,
)


# ── Constants ───────────────────────────────────────────────────────────────

PERMISSION_MODES = ("READ_ONLY", "WORKSPACE_WRITE", "FULL_ACCESS")

INTERRUPT_CURRENT_TASK = "interrupt_current_task"
TERMINATE_HARNESS = "terminate_harness"

MCP_TRUST_TIERS = ("trusted_system", "trusted_workspace", "untrusted_workspace")


# ── Error class ─────────────────────────────────────────────────────────────

class PermissionRefusedError(Exception):
    """The harness manifest does not support the requested permission mode."""


# ── Manifest lookup ─────────────────────────────────────────────────────────

_MANIFEST_BY_HARNESS: dict[str, dict] = {
    "codex": _MANIFEST_CODEX,
    "claude-code": _MANIFEST_CLAUDE_CODE,
    "opencode": _MANIFEST_OPENCODE,
    "dsh": _MANIFEST_DSH,
    "qwen": _MANIFEST_QWEN,
    "goose": _MANIFEST_GOOSE,
    "crush": _MANIFEST_CRUSH,
    "sweagent": _MANIFEST_SWEAGENT,
    "aider": _MANIFEST_AIDER,
    "whip": _MANIFEST_WHIP,
}


# ── Functions ───────────────────────────────────────────────────────────────

def effective_permission(harness: str, requested_mode: str) -> str:
    """Return the permission mode the harness will actually run under.

    The manifest's workspace booleans decide enforceability:

    * ``READ_ONLY`` requires ``workspace.read_only`` to be ``True``.
    * ``WORKSPACE_WRITE`` requires ``workspace.workspace_write`` to be ``True``.
    * ``FULL_ACCESS`` requires all three workspace booleans
      (``read_only``, ``workspace_write``, ``full_access``) to be ``True``.

    If the manifest cannot enforce the requested mode, raises
    :class:`PermissionRefusedError`.  Never silently downgrades and never
    silently broadens.
    """
    manifest = _MANIFEST_BY_HARNESS.get(harness)
    if manifest is None:
        raise PermissionRefusedError(
            f"unknown harness: {harness!r}"
        )

    ws = manifest["workspace"]

    if requested_mode == "READ_ONLY":
        if not ws["read_only"]:
            raise PermissionRefusedError(
                f"harness {harness!r} cannot enforce read-only "
                f"(workspace.read_only is False)"
            )
        return "READ_ONLY"

    if requested_mode == "WORKSPACE_WRITE":
        if not ws["workspace_write"]:
            raise PermissionRefusedError(
                f"harness {harness!r} cannot enforce workspace_write "
                f"(workspace.workspace_write is False)"
            )
        return "WORKSPACE_WRITE"

    if requested_mode == "FULL_ACCESS":
        if not (ws["read_only"] and ws["workspace_write"] and ws["full_access"]):
            raise PermissionRefusedError(
                f"harness {harness!r} cannot enforce full_access "
                f"(workspace={ws})"
            )
        return "FULL_ACCESS"

    raise PermissionRefusedError(f"unknown permission mode: {requested_mode!r}")


def mcp_tiers_allowed(mode: str) -> tuple:
    """Return the MCP trust tiers allowed for the given permission mode.

    | mode                | allowed tiers                              |
    |---------------------|--------------------------------------------|
    | READ_ONLY           | (trusted_system,)                          |
    | WORKSPACE_WRITE     | (trusted_system, trusted_workspace)        |
    | FULL_ACCESS         | (trusted_system, trusted_workspace)        |

    ``untrusted_workspace`` is **never** granted execution rights.
    """
    _TIER_MAP = {
        "READ_ONLY": ("trusted_system",),
        "WORKSPACE_WRITE": ("trusted_system", "trusted_workspace"),
        "FULL_ACCESS": ("trusted_system", "trusted_workspace"),
    }
    return _TIER_MAP.get(mode, ())
