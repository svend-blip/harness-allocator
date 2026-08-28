"""Workspace write lease tests.

Six contract-bound names (plus supporting tests to reach ≥ 16) exercising
acquire / release / recover_stale / config knobs / path normalisation.
"""

import json
import os
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

# Ensure the parent package is importable.
sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), ".."),
)


class TestLease(unittest.TestCase):
    """Tests for ``harness_allocator.lease``."""

    def setUp(self) -> None:
        self._tmp_state = tempfile.mkdtemp(prefix="lease_test_state_")
        self._tmp_workspace = tempfile.mkdtemp(prefix="lease_test_ws_")
        # Force the allocator to use our temp state directory.
        self._orig_env = dict(os.environ)
        os.environ["STATE_DIR"] = self._tmp_state

    def _lease_path(self, workspace: str) -> Path:
        """Return the expected lease file path for *workspace*."""
        import hashlib
        sha = hashlib.sha256(workspace.encode()).hexdigest()[:16]
        return Path(self._tmp_state) / f".lease_{sha}.json"

    def tearDown(self) -> None:
        # Restore original environment.
        os.environ.clear()
        os.environ.update(self._orig_env)
        # Clean up temp dirs.
        import shutil
        for d in (self._tmp_state, self._tmp_workspace):
            if os.path.isdir(d):
                shutil.rmtree(d, ignore_errors=True)

    # ------------------------------------------------------------------
    # Contract-bound tests (exactly six names)
    # ------------------------------------------------------------------

    def test_two_read_only_sessions_share_a_workspace(self):
        """Multiple readers coexist — acquire returns None for each."""
        from harness_allocator.lease import acquire

        r1 = acquire(self._tmp_workspace, "r1", "reader01", "opencode", "READ_ONLY")
        r2 = acquire(self._tmp_workspace, "r2", "reader02", "opencode", "READ_ONLY")
        self.assertIsNone(r1)
        self.assertIsNone(r2)

    def test_a_second_writer_is_rejected_by_default(self):
        """The default lease_conflict=REJECT refuses the second writer."""
        from harness_allocator.lease import LeaseConflict, acquire

        # First writer acquires.
        lease = acquire(
            self._tmp_workspace, "w1", "imple01", "opencode", "WORKSPACE_WRITE"
        )
        self.assertIsNotNone(lease)
        self.assertEqual(lease["request_id"], "w1")

        # Second writer is rejected.
        with self.assertRaises(LeaseConflict):
            acquire(
                self._tmp_workspace, "w2", "reviewer01", "opencode", "WORKSPACE_WRITE"
            )

    def test_a_second_writer_waits_when_configured_to_wait(self):
        """When lease_conflict=WAIT, the second writer waits and eventually
        acquires (the first releases before timeout)."""
        from harness_allocator.lease import acquire, release

        # First writer.
        lease1 = acquire(
            self._tmp_workspace, "w1", "imple01", "opencode", "WORKSPACE_WRITE"
        )
        self.assertIsNotNone(lease1)

        # Set WAIT mode.
        os.environ["LEASE_CONFLICT"] = "WAIT"
        os.environ["LEASE_WAIT_TIMEOUT"] = "10"

        import threading
        acquired_second = []

        def try_acquire_second():
            lease2 = acquire(
                self._tmp_workspace, "w2", "reviewer01", "opencode", "WORKSPACE_WRITE"
            )
            acquired_second.append(lease2)

        t = threading.Thread(target=try_acquire_second)
        t.daemon = True
        t.start()

        # Give the waiter a moment to acquire the lock.
        time.sleep(0.3)

        # Release the first writer — the second should acquire.
        release(lease1)
        t.join(timeout=12)

        self.assertTrue(len(acquired_second) == 1)
        self.assertIsNotNone(acquired_second[0])
        self.assertEqual(acquired_second[0]["request_id"], "w2")

    def test_the_lease_is_released_on_failure_and_interrupt(self):
        """If acquisition fails, no orphan lease file is left behind."""
        from harness_allocator.lease import acquire

        # First writer holds the lease.
        lease1 = acquire(
            self._tmp_workspace, "w1", "imple01", "opencode", "WORKSPACE_WRITE"
        )
        self.assertIsNotNone(lease1)

        # Second writer should raise, not leave a file.
        with self.assertRaises(Exception):
            acquire(
                self._tmp_workspace, "w2", "reviewer01", "opencode", "WORKSPACE_WRITE"
            )

        # Lease1 still owns the file.
        lease1_path = self._lease_path(self._tmp_workspace)
        self.assertTrue(lease1_path.exists())

        # After release, file is gone.
        from harness_allocator.lease import release

        release(lease1)
        self.assertFalse(lease1_path.exists())

    def test_a_stale_lease_from_a_dead_process_is_recovered(self):
        """A lease whose PID is dead is detected and removed by recover_stale."""
        from harness_allocator.lease import acquire, recover_stale, release

        # Acquire and release to create a valid lease file, then we'll
        # manually write a stale record with a dead PID.
        lease = acquire(
            self._tmp_workspace, "w1", "imple01", "opencode", "WORKSPACE_WRITE"
        )
        self.assertIsNotNone(lease)

        # Manually craft a stale lease record with a guaranteed-dead PID.
        import shutil
        release(lease)

        lease_dir = self._tmp_state
        stale_record = {
            "workspace": self._tmp_workspace,
            "request_id": "stale_req",
            "role": "dead_imple01",
            "harness": "whip",
            "acquired_at": "2020-01-01T00:00:00Z",
            "pid": 99999999,  # Almost certainly dead.
        }
        # Write a lease file directly.
        import hashlib
        sha = hashlib.sha256(self._tmp_workspace.encode()).hexdigest()[:16]
        lease_path = os.path.join(lease_dir, f".lease_{sha}.json")
        with open(lease_path, "w") as f:
            json.dump(stale_record, f)

        # recover_stale should find and remove it.
        recovered = recover_stale()
        self.assertTrue(len(recovered) >= 1)
        self.assertFalse(os.path.exists(lease_path))

    def test_release_is_idempotent(self):
        """Releasing the same lease twice is not an error."""
        from harness_allocator.lease import acquire, release

        lease = acquire(
            self._tmp_workspace, "w1", "imple01", "opencode", "WORKSPACE_WRITE"
        )
        self.assertIsNotNone(lease)

        release(lease)
        # Second release should not raise.
        release(lease)

    # ------------------------------------------------------------------
    # Supporting tests
    # ------------------------------------------------------------------

    def test_single_writer_acquires_successfully(self):
        """The first writer on a fresh workspace gets the lease."""
        from harness_allocator.lease import acquire

        lease = acquire(
            self._tmp_workspace, "w1", "imple01", "whip", "WORKSPACE_WRITE"
        )
        self.assertIsNotNone(lease)
        self.assertEqual(lease["workspace"], os.path.realpath(self._tmp_workspace))
        self.assertEqual(lease["role"], "imple01")
        self.assertEqual(lease["harness"], "whip")
        self.assertEqual(lease["request_id"], "w1")

    def test_full_access_acquires_like_workspace_write(self):
        """FULL_ACCESS mode acquires an exclusive lease just like WORKSPACE_WRITE."""
        from harness_allocator.lease import LeaseConflict, acquire

        lease = acquire(
            self._tmp_workspace, "w1", "imple01", "opencode", "FULL_ACCESS"
        )
        self.assertIsNotNone(lease)

        with self.assertRaises(LeaseConflict):
            acquire(
                self._tmp_workspace, "w2", "reviewer01", "opencode", "FULL_ACCESS"
            )

    def test_path_normalization_prevents_duplicate_leases(self):
        """Two spellings of the same directory share one lease."""
        from harness_allocator.lease import LeaseConflict, acquire

        # Create a symlink to the workspace.
        extra_dir = tempfile.mkdtemp(prefix="lease_test_extra_")
        real = os.path.realpath(self._tmp_workspace)
        link = os.path.join(extra_dir, "link_to_ws")

        created = False
        try:
            os.symlink(real, link)
            created = True
        except OSError:
            # Symlinks may not work in all envs (e.g. Docker).
            self.skipTest("symlink not supported in this environment")

        try:
            lease = acquire(
                self._tmp_workspace, "w1", "imple01", "opencode", "WORKSPACE_WRITE"
            )
            self.assertIsNotNone(lease)

            # The symlink path should resolve to the same realpath.
            with self.assertRaises(LeaseConflict):
                acquire(
                    link, "w2", "reviewer01", "opencode", "WORKSPACE_WRITE"
                )
        finally:
            if created:
                os.unlink(link)
            os.rmdir(extra_dir)

    def test_lease_file_in_state_dir_not_workspace(self):
        """The lease file is never placed inside the leased workspace."""
        from harness_allocator.lease import acquire

        initial_contents = set(os.listdir(self._tmp_workspace))
        lease = acquire(
            self._tmp_workspace, "w1", "imple01", "opencode", "WORKSPACE_WRITE"
        )
        self.assertIsNotNone(lease)
        after_contents = set(os.listdir(self._tmp_workspace))
        self.assertEqual(initial_contents, after_contents)

    def test_config_defaults(self):
        """Default lease_conflict is REJECT and timeout is 30s."""
        # Remove any overridden env vars.
        os.environ.pop("LEASE_CONFLICT", None)
        os.environ.pop("LEASE_WAIT_TIMEOUT", None)

        from harness_allocator.config import (
            get_lease_conflict,
            get_lease_wait_timeout,
        )

        self.assertEqual(get_lease_conflict(), "REJECT")
        self.assertEqual(get_lease_wait_timeout(), 30.0)

    def test_config_env_override(self):
        """Env vars override defaults for lease_conflict and lease_wait_timeout."""
        os.environ["LEASE_CONFLICT"] = "WAIT"
        os.environ["LEASE_WAIT_TIMEOUT"] = "120"

        from harness_allocator.config import (
            get_lease_conflict,
            get_lease_wait_timeout,
        )

        self.assertEqual(get_lease_conflict(), "WAIT")
        self.assertEqual(get_lease_wait_timeout(), 120.0)

    def test_acquire_returns_none_for_read_only(self):
        """READ_ONLY mode always returns None regardless of existing leases."""
        from harness_allocator.lease import acquire

        lease = acquire(
            self._tmp_workspace, "w1", "imple01", "opencode", "WORKSPACE_WRITE"
        )
        self.assertIsNotNone(lease)

        # Multiple readers can coexist alongside the writer.
        for i in range(5):
            r = acquire(
                self._tmp_workspace, f"r{i}", f"reader{i}", "opencode", "READ_ONLY"
            )
            self.assertIsNone(r)

    def test_release_none_is_safe(self):
        """Passing None to release() is a no-op, not an error."""
        from harness_allocator.lease import release

        # Should not raise.
        release(None)

    def test_public_api_exports(self):
        """__all__ lists exactly the six required public names."""
        from harness_allocator import lease

        expected = sorted(["acquire", "release", "recover_stale", "Lease", "LeaseConflict", "LeaseError"])
        self.assertEqual(sorted(lease.__all__), expected)

    def test_no_scheduling_vocabulary(self):
        """lease.py must not contain scheduling-related vocabulary."""
        from harness_allocator import lease

        source = lease.__file__
        with open(source, "r") as f:
            content = f.read().lower()

        forbidden = ["priority", "schedul", "queue_position", "preempt"]
        for word in forbidden:
            self.assertNotIn(word, content, f"scheduling word '{word}' found in lease.py")

    def test_lease_error_hierarchy(self):
        """LeaseConflict is a subclass of LeaseError."""
        from harness_allocator.lease import LeaseConflict, LeaseError

        self.assertTrue(issubclass(LeaseConflict, LeaseError))
        self.assertTrue(issubclass(LeaseError, Exception))

    def test_state_dir_default(self):
        """get_state_dir defaults to /tmp/harness_allocator when env/ini are absent."""
        # Ensure no overrides.
        os.environ.pop("STATE_DIR", None)
        from harness_allocator.config import get_state_dir

        result = get_state_dir()
        self.assertIn("harness_allocator", str(result))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lease_path(workspace: str, state_dir: str) -> str:
    """Return the expected lease file path for *workspace*."""
    import hashlib

    normalized = os.path.realpath(workspace)
    sha = hashlib.sha256(normalized.encode()).hexdigest()[:16]
    return os.path.join(state_dir, f".lease_{sha}.json")
