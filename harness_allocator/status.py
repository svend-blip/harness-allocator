"""Readiness and result status tokens for the Harness Allocator.

The persistent terminal and the one-shot ``execute`` boundary share these
tokens so callers can compare statuses without importing the terminal loop.

``DUPLICATE_REQUEST`` is a terminal-only readiness outcome: it is what the
persistent loop reports (and returns to READY) when a request whose identity
was already completed arrives again without an explicit ``retry`` flag. It is
never returned by :func:`~harness_allocator.invoke.execute`.
"""

from __future__ import annotations

READY = "READY"
RUNNING = "RUNNING"
SUCCESS = "SUCCESS"
ERROR = "ERROR"
DUPLICATE_REQUEST = "DUPLICATE_REQUEST"
CANCELLED = "CANCELLED"
INTERRUPTING = "INTERRUPTING"
CLEANUP = "CLEANUP"

# The status banner uses these values when a caller has not supplied a
# configuration/runtime value.  Keep them explicit so an absent value is never
# mistaken for permission or integration state.
UNKNOWN = "unknown"
NOT_CONFIGURED = "not configured"

_SECRET_MARKERS = ("api_key", "token", "secret", "password", "credential")


def status_value(info, key, default=UNKNOWN, choices=()):
    """Return one bounded, single-line value for the runtime status banner.

    ``status.py`` is shared by the standalone package and the DPMtF seam.  The
    banner deliberately has a small whitelist of fields, but callers can still
    pass an accidental secret in a field.  This helper collapses whitespace,
    rejects credential-like labels, and normalises values outside an allowlist
    to the supplied default.
    """
    if not isinstance(info, dict):
        return default
    value = info.get(key)
    if value is None:
        return default
    text = " ".join(str(value).split())
    lowered = text.lower()
    if not text or any(marker in lowered for marker in _SECRET_MARKERS):
        return default
    if choices:
        allowed = {str(choice).lower() for choice in choices}
        if lowered not in allowed:
            return default
        return next(choice for choice in choices if str(choice).lower() == lowered)
    return text[:512]
