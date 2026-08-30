"""The Harness Adapter Interface — formal lifecycle contract.

This module codifies the SCOPE §2 lifecycle as an explicit, importable
contract. The harness allocator previously had no formal interface —
adapters were a convention (three ``build_*`` functions plus a manifest).
This file creates the formal boundary.

The contract binds *reporting*, not uniform ability: an adapter that
cannot perform a lifecycle operation raises ``UnsupportedOperation`` —
it never silently emulates, and it never silently succeeds.

The contract exposes a conformance surface that the generic test suite
in ``tests/test_adapter_contract.py`` drives against every registered
harness. The ten existing adapters must pass this suite unchanged,
which is what makes the contract a description of reality rather than
an aspiration.
"""

from __future__ import annotations


class UnsupportedOperation(Exception):
    """An operation the harness adapter does not support.

    Raised by an adapter method when the underlying harness cannot perform
    the requested lifecycle operation. The adapter must raise this
    exception — it must never silently emulate the operation, and it must
    never silently report success.

    Each instance carries the operation name and the harness key in the
    message for traceability through the test suite and the execute layer.

    The lifecycle operations are defined by :data:`LIFECYCLE`.
    """

    def __init__(self, operation: str, harness: str) -> None:
        self.operation = operation
        self.harness = harness
        super().__init__(
            f"{harness!r} does not support lifecycle operation "
            f"{operation!r}"
        )


#: The SCOPE §2 lifecycle — ten ordered operations that every adapter
#: surface maps onto. The tuple is the canonical ordering and the
#: authoritative list used by the generic contract suite.
#:
#: Each name is the canonical verb form that adapter methods use:
#:
#: - **probe**          — ``build_probe_*`` / ``is_native_harness``
#: - **capabilities**   — ``get_capabilities`` (capabilities.py surface)
#: - **prepare**        — environment validation, config projection
#: - **start**          — ``build_*_invocation`` / ``build_*_argv``
#: - **send**           — submitting a task/prompt to a running session
#: - **status**         — querying session/process status
#: - **interrupt**      — ``build_interrupt_*`` / interrupt handling
#: - **collect**        — gathering output and result after completion
#: - **resume**         — ``build_resume_*`` / session resumption
#: - **cleanup**        — ``cleanup_*`` / resource reclamation
LIFECYCLE: tuple[str, ...] = (
    "probe",
    "capabilities",
    "prepare",
    "start",
    "send",
    "status",
    "interrupt",
    "collect",
    "resume",
    "cleanup",
)
