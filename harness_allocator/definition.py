"""Harness identity resolution and environment requirements.

Resolves a role-shaped value to a harness key, and knows which native harnesses
require which credentials. It knows nothing about model selection: the resolved
model target is supplied by the caller (Model Allocator, upstream of this
package) and is only ever passed through — never resolved, never defaulted,
never substituted here.

Harness-generic by construction: it never reads a database and knows nothing
about any caller's flows, verdicts, roles or governance.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

#: Harnesses the allocator launches directly (no model-allocator adapter).
NATIVE_HARNESSES = ("dsh", "codex")

#: env var -> human description, for a safe error that never prints a value.
REQUIRED_ENV = {
    "dsh": {"DEEPSEEK_API_KEY": "DeepSeek Harness direct DeepSeek API"},
    "codex": {"MINIMAX_API_KEY": "Codex MiniMax M3 provider"},
}


@dataclass
class HarnessDefinition:
    """A resolved harness identity: role key and harness key only.

    The model is deliberately absent. The model target is resolved upstream by
    Model Allocator and passed alongside this definition; a harness definition
    describes harness identity/configuration, not model selection.
    """

    role: str = ""
    harness: str = ""

    @classmethod
    def from_role(cls, role) -> "HarnessDefinition":
        """Resolve a role-shaped value into a harness identity (role + harness)."""
        if isinstance(role, cls):
            return role
        return cls(
            role=resolve_role_key(role),
            harness=resolve_harness(role),
        )


def resolve_role_key(role) -> str:
    """The human-facing role key from a role, for banner/display only."""
    if isinstance(role, str):
        return role
    if isinstance(role, dict):
        return role.get("role_key") or role.get("role") or ""
    for attr in ("role", "role_key"):
        value = getattr(role, attr, None)
        if value:
            return str(value)
    return ""


def resolve_harness(role) -> str:
    """The harness key for a role (empty string when not specified).

    Accepts a mapping (``allocator_client`` or ``harness`` key), an object with
    a ``harness`` attribute, or a bare harness-name string. There is NO silent
    substitution: an absent/unknown harness yields ``""``, never a guessed
    default — the caller decides how to handle an unresolved harness.
    """
    if isinstance(role, str):
        return role.strip()
    if isinstance(role, dict):
        return (role.get("allocator_client") or role.get("harness") or "").strip()
    for attr in ("harness", "allocator_client"):
        value = getattr(role, attr, None)
        if value:
            return str(value).strip()
    return ""


def model_target_identity(model_target) -> str:
    """A display-safe identity string for an already-resolved model target.

    Pure passthrough for display/audit only — never a resolver, never a default,
    never a substitution. Accepts a bare string, a mapping with an ``identity``
    or ``model`` key, or an object with an ``identity``/``model`` attribute.
    """
    if model_target is None:
        return ""
    if isinstance(model_target, str):
        return model_target.strip()
    if isinstance(model_target, dict):
        return str(model_target.get("identity") or model_target.get("model") or "").strip()
    for attr in ("identity", "model"):
        value = getattr(model_target, attr, None)
        if value:
            return str(value).strip()
    return ""


def is_native(harness) -> bool:
    """True when the allocator builds the launch command itself for this harness."""
    return harness in NATIVE_HARNESSES


def missing_env(harness) -> list:
    """Environment variable names required by a native harness that are unset.

    Returns a list (possibly empty). A non-native harness returns an empty list.
    """
    required = REQUIRED_ENV.get(harness, {})
    return [name for name in required if not os.environ.get(name)]


def describe_missing(harness, missing) -> str:
    """A one-line, human-safe error naming what is missing (never the value)."""
    required = REQUIRED_ENV.get(harness, {})
    names = ", ".join(f"{name} ({required[name]})" for name in missing)
    return f"Harness '{harness}' requires environment variable(s): {names}"
