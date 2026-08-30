"""Harness Allocator web UI — read-only browse frontend (port 9142).

Deliberately isolated: the ``harness_allocator`` package proper is
stdlib-only, and importing it must stay dependency-free. This subpackage
is imported ONLY when the UI is run (``python3 -m harness_allocator.web``)
and is the one place FastAPI/uvicorn are allowed to appear.
"""
