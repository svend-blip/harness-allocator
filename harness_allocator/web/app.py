"""Harness Allocator UI — read-only browse frontend.

Mirrors the model-allocator pattern: a small FastAPI app that renders
what there is to choose between. It imports the ``harness_allocator``
package READ-ONLY — it never writes to the allocator's repository,
config, or state.

Folded in from the standalone /home/svend/harness-allocator-ui repo
(2026-08-30 alignment): one repo, one standard, the model-allocator
``web/`` shape. Changes from the standalone: static assets resolved
relative to this file (not cwd), the login-shell PATH capture is lazy
(no subprocess at import time), and the default bind is loopback —
the UI has no auth, so exposing it beyond the machine is an explicit
HARNESS_WEB_HOST decision, never a default.
"""
import os
import shutil
import subprocess
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import harness_allocator.capabilities as caps
import harness_allocator.config as ha_config

WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"

app = FastAPI(title="Harness Allocator UI")

# Binary resolution: use the allocator's own config getters where they
# exist (env/ini/default precedence lives THERE, not here); the three
# harnesses without a getter use their conventional launcher names.
_FALLBACK_BINS = {"claude-code": "claude", "opencode": "opencode", "whip": "whip"}


# The service may run with a minimal PATH (systemd, sandbox). Rather
# than hardcode bin directories, resolve availability against the
# USER'S OWN login-shell PATH: the login shell sources the user's
# profile, so whatever the user can launch from a terminal is what the
# UI reports. Captured lazily on first use (never at import — a module
# import must not spawn a shell) and cached. Override order:
#   1. HA_UI_PATH environment variable (explicit override)
#   2. `bash -lc 'printf %s "$PATH"'` (the login shell's PATH)
#   3. the service process's own PATH (last resort)
_path_cache: dict = {}


def _search_path() -> str:
    if "path" in _path_cache:
        return _path_cache["path"]
    override = os.environ.get("HA_UI_PATH")
    if override:
        _path_cache["path"] = override
        return override
    resolved = os.environ.get("PATH", "")
    try:
        out = subprocess.run(
            ["bash", "-lc", 'printf %s "$PATH"'],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            resolved = out.stdout.strip()
    except Exception:
        pass
    _path_cache["path"] = resolved
    return resolved


def _resolve_bin(name: str) -> str:
    getter = getattr(ha_config, f"get_{name.replace('-', '_')}_bin", None)
    if getter is not None:
        try:
            return getter()
        except Exception:
            pass
    return _FALLBACK_BINS.get(name, name)


def _describe(name: str) -> dict:
    binary = _resolve_bin(name)
    # dsh launches via npx per its config docstring; availability follows
    # the first token of the configured command line.
    probe = binary.split()[0]
    path = shutil.which(probe, path=_search_path())
    return {
        "name": name,
        "bin": binary,
        "resolved_path": path,
        "status": "AVAILABLE" if path else "MISSING",
        "capabilities": caps.get_capabilities(name),
    }


@app.get("/api/health")
def health() -> dict:
    return {"status": "healthy", "app": "Harness Allocator"}


@app.get("/api/harnesses")
def harnesses() -> dict:
    return {
        "supported": [_describe(n) for n in caps.SUPPORTED_HARNESSES],
        "experimental": [_describe(n) for n in caps.EXPERIMENTAL_HARNESSES],
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def run():
    """Entry point: ``python3 -m harness_allocator.web``."""
    import uvicorn
    port = int(os.environ.get("HARNESS_WEB_PORT", "9142"))
    # Loopback by default: the UI has no auth. Widening the bind is an
    # explicit decision (HARNESS_WEB_HOST=0.0.0.0), never a default.
    host = os.environ.get("HARNESS_WEB_HOST", "127.0.0.1")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    run()
