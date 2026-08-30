"""Tests for ``harness_allocator.web`` — the read-only browse UI.

The web subpackage is the ONE place third-party imports (FastAPI) are
allowed; the allocator package proper stays stdlib-only. These tests
skip cleanly where FastAPI is not installed — the UI is optional, the
package contract is not.

Pins:
- Importing ``harness_allocator`` alone must NOT import the web
  subpackage (stdlib-only contract of the core package).
- The roster endpoints serve every supported + experimental harness
  with the AVAILABLE/MISSING shape.
- ``_search_path`` honours the HA_UI_PATH override without spawning a
  shell, and nothing spawns a shell at import time.
- The default bind is loopback (no-auth UI must not default to 0.0.0.0).
"""
import importlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness_allocator.capabilities import (  # noqa: E402
    EXPERIMENTAL_HARNESSES,
    SUPPORTED_HARNESSES,
)


def test_core_package_import_does_not_pull_the_web_subpackage():
    """`import harness_allocator` stays stdlib-only: no web, no fastapi."""
    for mod in list(sys.modules):
        if mod.startswith("harness_allocator.web"):
            del sys.modules[mod]
    importlib.import_module("harness_allocator")
    assert not any(m.startswith("harness_allocator.web") for m in sys.modules)


fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from harness_allocator.web import app as web_app  # noqa: E402


@pytest.fixture()
def client():
    return TestClient(web_app.app)


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "healthy"


def test_harnesses_covers_the_whole_roster(client, monkeypatch):
    monkeypatch.setenv("HA_UI_PATH", os.environ.get("PATH", ""))
    web_app._path_cache.clear()
    body = client.get("/api/harnesses").json()
    assert [h["name"] for h in body["supported"]] == list(SUPPORTED_HARNESSES)
    assert [h["name"] for h in body["experimental"]] == list(EXPERIMENTAL_HARNESSES)
    for h in body["supported"] + body["experimental"]:
        assert h["status"] in ("AVAILABLE", "MISSING")
        assert isinstance(h["capabilities"], dict)


def test_search_path_override_wins_without_shell(monkeypatch):
    monkeypatch.setenv("HA_UI_PATH", "/nonexistent-override")
    web_app._path_cache.clear()

    def _boom(*a, **k):  # any subprocess here is a contract breach
        raise AssertionError("HA_UI_PATH override must not spawn a shell")

    monkeypatch.setattr(web_app.subprocess, "run", _boom)
    assert web_app._search_path() == "/nonexistent-override"
    web_app._path_cache.clear()


def test_default_bind_is_loopback(monkeypatch):
    """run() must default HARNESS_WEB_HOST to 127.0.0.1 — the UI has no auth."""
    monkeypatch.delenv("HARNESS_WEB_HOST", raising=False)
    monkeypatch.delenv("HARNESS_WEB_PORT", raising=False)
    captured = {}

    class _FakeUvicorn:
        @staticmethod
        def run(app, host=None, port=None):
            captured["host"] = host
            captured["port"] = port

    monkeypatch.setitem(sys.modules, "uvicorn", _FakeUvicorn)
    web_app.run()
    assert captured == {"host": "127.0.0.1", "port": 9142}
