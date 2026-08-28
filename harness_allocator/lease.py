"""Workspace write lease — safety enforcement only.

Exclusive write grip per workspace.  The lease exists solely to ensure
that at most one session holds a write grip on a given workspace at any
time.

Public API
----------
acquire / release / recover_stale / Lease / LeaseConflict / LeaseError
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_MISSING = object()

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

__all__ = [
    "acquire",
    "release",
    "recover_stale",
    "Lease",
    "LeaseConflict",
    "LeaseError",
]


class LeaseError(Exception):
    """Base exception for lease operations."""


class LeaseConflict(LeaseError):
    """A write lease is already held by another session on this workspace."""


# ---------------------------------------------------------------------------
# Lease dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Lease:
    """A workspace write lease record.

    Fields per SCOPE §8 identity contract.
    """
    workspace: str
    request_id: str
    role: str
    harness: str
    acquired_at: str
    pid: int | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.pid is None:
            object.__setattr__(self, "pid", os.getpid())

    # ── dict-like compatibility for release() and recover_stale() ──

    def get(self, key: str, default=None):
        """Dict-like .get() for backward compatibility."""
        try:
            return getattr(self, key)
        except AttributeError:
            return default

    def __getitem__(self, key: str):
        """Dict-like [] access for backward compatibility."""
        v = getattr(self, key, _MISSING)
        if v is _MISSING:
            raise KeyError(key)
        return v

    def keys(self):
        """Dict-like .keys() for backward compatibility."""
        return ("workspace", "request_id", "role", "harness", "acquired_at", "pid")


def _make_lease(
    workspace: str,
    request_id: str,
    role: str,
    harness: str,
    acquired_at: str,
    pid: int | None = None,
) -> Lease:
    """Return a Lease instance."""
    return Lease(
        workspace=workspace,
        request_id=request_id,
        role=role,
        harness=harness,
        acquired_at=acquired_at,
        pid=pid,
    )


def _state_dir() -> Path:
    """State directory for allocator lease files.

    Determined via config.py getters (``get_state_dir``) so that the
    calling chain controls the location.  Falls back to the tempfile
    directory if the getter is unavailable.
    """
    try:
        from harness_allocator.config import get_state_dir  # noqa: F811

        return get_state_dir()
    except (ImportError, AttributeError):
        return Path(tempfile.gettempdir()) / "harness_allocator"


def _lease_file_path(normalized_workspace: str) -> Path:
    """Return the path to the lease file for *normalized_workspace*.

    The path is ``<state_dir>/.lease_<sha256>.json`` so that two
    different spellings of the same directory never hold separate leases.
    """
    state_dir = _state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    sha = hashlib.sha256(normalized_workspace.encode()).hexdigest()[:16]
    return state_dir / f".lease_{sha}.json"


# ---------------------------------------------------------------------------
# acquire
# ---------------------------------------------------------------------------

def acquire(
    workspace: str,
    request_id: str,
    role: str,
    harness: str,
    mode: str,
) -> Lease | None:
    """Acquire (or short-circuit) a write lease for *workspace*.

    Parameters
    ----------
    workspace :
        Absolute path to the workspace.  Normalised via ``realpath()``.
    request_id :
        Allocator request the lease is traceable to.
    role :
        Role name (e.g. ``"imple01"``).
    harness :
        Harness key (e.g. ``"whip"``, ``"opencode"``).
    mode :
        One of ``"READ_ONLY"``, ``"WORKSPACE_WRITE"``, ``"FULL_ACCESS"``.

    Returns
    -------
    None
        When *mode* is ``"READ_ONLY"`` — readers are unlimited.
    Lease
        The lease record when a write-mode lease is acquired.

    Raises
    ------
    LeaseConflict
        When another session already holds the write lease and the
        configured conflict behaviour is ``REJECT`` (the default).
    """
    from harness_allocator.config import (
        get_lease_conflict,
        get_lease_wait_timeout,
    )

    # Normalise — two spellings of the same directory share one lease.
    normalized = os.path.realpath(workspace)

    if mode == "READ_ONLY":
        return None

    conflict_mode = get_lease_conflict()
    wait_timeout = get_lease_wait_timeout() if conflict_mode == "WAIT" else 0.0

    lease_file = _lease_file_path(normalized)

    import time

    deadline: Optional[float] = None
    if conflict_mode == "WAIT" and wait_timeout > 0:
        deadline = time.monotonic() + wait_timeout

    while True:
        fd: Optional[int] = None
        try:
            fd = os.open(str(lease_file), os.O_RDWR | os.O_CREAT, 0o644)
        except OSError:
            # Fallback: try atomic write pattern (rare on Linux).
            tmp = lease_file.with_suffix(".tmp")
            tmp.touch(exist_ok=True)
            try:
                fd = os.open(str(tmp), os.O_RDWR | os.O_CREAT, 0o644)
            except OSError:
                tmp.unlink(missing_ok=True)
                raise
            try:
                os.rename(str(tmp), str(lease_file))
            except OSError:
                os.close(fd)
                fd = None
                raise
            try:
                fd = os.open(str(lease_file), os.O_RDWR | os.O_CREAT, 0o644)
            except OSError:
                raise

        try:
            # Non-blocking flock attempt.
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (IOError, OSError):
                # Lease file is locked — another process has it.
                os.close(fd)
                fd = None

                if conflict_mode == "REJECT":
                    raise LeaseConflict(
                        f"Write lease for {normalized} already held by another session."
                    )

                # WAIT mode — check deadline.
                if deadline is not None and time.monotonic() >= deadline:
                    raise LeaseConflict(
                        f"Timed out waiting for write lease on {normalized} "
                        f"after {wait_timeout}s."
                    )

                # Brief sleep before retry (bounded).
                time.sleep(0.05)
                continue

            # We hold the lock — read any existing record.
            try:
                os.lseek(fd, 0, 0)
                data = os.read(fd, 65536)
                existing = json.loads(data.decode()) if data.strip() else None
            except (json.JSONDecodeError, OSError):
                existing = None

            # If there's already a lease, reject (we hold the lock so it's
            # guaranteed to be the same file we tried to lock).
            if existing:
                os.close(fd)
                fd = None
                if conflict_mode == "REJECT":
                    raise LeaseConflict(
                        f"Write lease for {normalized} already held by "
                        f"{existing.get('role', '?')} "
                        f"(request {existing.get('request_id', '?')})."
                    )

                # WAIT — go back to the top to retry after sleep.
                if deadline is not None and time.monotonic() >= deadline:
                    raise LeaseConflict(
                        f"Timed out waiting for write lease on {normalized} "
                        f"after {wait_timeout}s."
                    )
                time.sleep(0.05)
                continue

            # Write our lease record.
            import datetime
            now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
            record = _make_lease(
                workspace=normalized,
                request_id=request_id,
                role=role,
                harness=harness,
                acquired_at=now_iso,
            )
            encoded = json.dumps(asdict(record), indent=2).encode()
            os.lseek(fd, 0, 0)
            os.write(fd, encoded)
            os.ftruncate(fd, len(encoded))

            return record

        finally:
            if fd is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)


# ---------------------------------------------------------------------------
# release
# ---------------------------------------------------------------------------

def release(lease: Lease | dict | None) -> None:
    """Release a previously acquired write lease.

    Idempotent — releasing twice is not an error.
    """
    if lease is None:
        return

    normalized = os.path.realpath(lease.get("workspace"))
    lease_file = _lease_file_path(normalized)

    fd: Optional[int] = None
    try:
        fd = os.open(str(lease_file), os.O_RDWR | os.O_CREAT, 0o644)
    except OSError:
        return  # File gone — idempotent.

    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        # Verify the lease still belongs to us (pid match).
        try:
            os.lseek(fd, 0, 0)
            data = os.read(fd, 65536)
            current = json.loads(data.decode()) if data.strip() else None
        except (json.JSONDecodeError, OSError):
            current = None

        if current and current.get("pid") == os.getpid():
            # Atomically remove the lease file.
            os.close(fd)
            fd = None
            try:
                os.unlink(str(lease_file))
            except OSError:
                pass  # Gone already — benign.
            return

        # If we don't own it anymore, just close and consider it released.
        logger.info(
            "release: lease for %s no longer owned by pid %d — skipping unlink",
            normalized,
            os.getpid(),
        )

    finally:
        if fd is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


# ---------------------------------------------------------------------------
# recover_stale
# ---------------------------------------------------------------------------

def recover_stale() -> list[dict]:
    """Recover leases whose owning process is dead or expired.

    Returns
    -------
    list[dict]
        List of recovered lease records. Each entry is logged with its
        full identity.
    """
    import datetime

    from harness_allocator.config import get_lease_wait_timeout

    state_dir = _state_dir()
    timeout = get_lease_wait_timeout()
    recovered: list[dict] = []

    if not state_dir.exists():
        return recovered

    for candidate in state_dir.glob(".lease_*.json"):
        try:
            with open(candidate, "r") as f:
                record = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue  # Corrupt or half-written — skip.

        pid = record.get("pid")
        workspace = record.get("workspace", "?")
        request_id = record.get("request_id", "?")
        role = record.get("role", "?")
        harness = record.get("harness", "?")
        acquired_at = record.get("acquired_at", "?")

        stale = False
        reason: str = ""

        # 1. Check if the owning process is alive.
        if pid:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                stale = True
                reason = f"pid {pid} dead"
            except PermissionError:
                # Process exists but we lack permission — check age.
                stale = True
                reason = f"pid {pid} (permission denied, assuming stale)"
            # else: process is alive — check age next.
        else:
            stale = True
            reason = "no pid recorded"

        # 2. Check age against timeout.
        if not stale and timeout > 0:
            try:
                ts_str = str(acquired_at).replace("Z", "+00:00")
                acquired_dt = (
                    datetime.datetime.fromisoformat(ts_str)
                    .replace(tzinfo=datetime.timezone.utc)
                )
                age = (
                    datetime.datetime.now(datetime.timezone.utc) - acquired_dt
                ).total_seconds()
                if age > timeout:
                    stale = True
                    reason = (
                        f"lease age {age:.0f}s exceeds timeout {timeout:.0f}s"
                    )
            except (ValueError, TypeError):
                stale = True
                reason = f"unparseable acquired_at {acquired_at!r}"

        if stale:
            recovered.append(record)
            logger.warning(
                "recover_stale: %s — workspace=%s, request_id=%s, role=%s, "
                "harness=%s, acquired_at=%s — %s",
                candidate.name,
                workspace,
                request_id,
                role,
                harness,
                acquired_at,
                reason,
            )
            # Remove the stale lease file.
            try:
                candidate.unlink()
            except OSError:
                pass  # Gone already — benign.

    return recovered
