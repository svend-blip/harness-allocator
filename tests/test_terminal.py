"""Terminal banner tests (Run 069).

Tests that the terminal module correctly renders PERMISSION and REQUEST_ID
banner fields.  Uses only the public API surface:

    render_banner(role, harness_key, model_target, cwd, flow="",
                  status_info=None, runtime_info=None)

Does NOT read any source files in harness_allocator/.
"""

from __future__ import annotations

from harness_allocator.terminal import render_banner


def test_permission_banner_contains_label() -> None:
    """render_banner with permission in status_info must include the label."""
    banner = render_banner(
        role="implementer",
        harness_key="whip",
        model_target="test-model",
        cwd="/tmp/test",
        status_info={"permission": "workspace-write"},
    )
    assert "Permission:" in banner or "Permission" in banner, (
        "render_banner must include Permission label"
    )
    assert "workspace-write" in banner, (
        "render_banner must include the permission value"
    )


def test_request_id_banner_contains_label_and_id() -> None:
    """render_banner with request_id in status_info must include both."""
    banner = render_banner(
        role="implementer",
        harness_key="whip",
        model_target="test-model",
        cwd="/tmp/test",
        status_info={"request_id": "req-12345"},
    )
    assert "Request ID:" in banner or "Request ID" in banner, (
        "render_banner must include Request ID label"
    )
    assert "req-12345" in banner, (
        "render_banner must include the request ID value"
    )


def test_banner_with_both_permission_and_request_id() -> None:
    """render_banner with both fields present renders them together."""
    banner = render_banner(
        role="implementer",
        harness_key="whip",
        model_target="test-model",
        cwd="/tmp/test",
        status_info={
            "permission": "full-access",
            "request_id": "req-abcde",
        },
    )
    assert "full-access" in banner, "banner must include permission value"
    assert "req-abcde" in banner, "banner must include request ID value"
