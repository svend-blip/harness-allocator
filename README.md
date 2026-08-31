# Harness Allocator

A standalone, **harness-neutral** allocator for coding harnesses. It resolves a
role to a harness key, builds the invocation, runs one-shot turns with an
argv-style task transport, and reports status — **independent of DPMtF**
flows, verdicts, roles, governance, dispatch, and the bridge database.

This is an **optional companion project** to DPMtF-WebUI (DPMtF —
Deterministic Process Management to Finalisation: a deterministic
multi-agent process orchestration framework for taking defined work from
intent to verified finalisation through governed flows, steps, roles,
harnesses, models, gates, and artifacts). DPMtF keeps its own
dispatch, database, roles, verdicts and governance; it only ever needs to ask
this package for behaviour, not for command strings.

## Overview

**The roster** (derived from `capabilities.SUPPORTED_HARNESSES` /
`EXPERIMENTAL_HARNESSES` — the count is never hand-written in prose):

| Harness | LaunchSpec mode | Launch owner | Context reset |
|---------|-----------------|--------------|---------------|
| codex | resident_tui | harness_allocator | restart |
| claude-code | resident_tui | model_allocator | `/clear` |
| opencode | resident_tui | model_allocator | `/new` |
| dsh | terminal_wrapped | harness_allocator | restart |
| qwen | one_shot | harness_allocator | restart |
| goose | one_shot | harness_allocator | restart |
| crush | one_shot | harness_allocator | restart |
| whip | one_shot | model_allocator | restart |
| simple-harness | one_shot | harness_allocator | restart |
| sweagent *(experimental)* | one_shot | harness_allocator | restart |
| aider *(experimental)* | one_shot | harness_allocator | restart |

Experimental harnesses are refused pre-subprocess unless explicitly enabled
(`experimental_enabled_harnesses`). `launch_owner` is computed from
`NATIVE_HARNESSES` membership: claude-code/opencode/whip are described here
but launched by model-allocator's client adapters — a declared asymmetry.

**Model boundary.** Harness Allocator does **not** resolve, select, replace,
or own the model. Model Allocator resolves the model target first; the caller
passes the already-resolved `model_target` here, and the allocator only
renders it into the harness's native CLI. There is no `resolve_model()` and
no silent model or harness substitution.

**What it owns:**

- **Identity resolution** — `resolve_harness`, `resolve_role_key`,
  `HarnessDefinition.from_role` (role → harness key). No model.
- **Command generation** — `build_launch_argv` / `build_task_argv` (and
  per-harness builders such as `build_dsh_argv`, `build_qwen_argv`,
  `build_simple_harness_argv`), rendering a passed-through `model_target`.
  Launch forms start a session; task forms run one prompt.
- **Per-harness declarations** — `get_capabilities` (the capability
  manifest), `get_launch_spec` (mode, anchor, required env, activity
  markers), `get_stop_spec` (signal ladder, grace, verification), and
  `get_reset_spec` (how a live session's context is reset: an in-session
  slash command, or restart). All fail closed against the roster.
- **One-shot execution** — `execute(role, harness, model_target, cwd, task)`
  returning `{ status, output, error, elapsed, pid, request_id, ... }`.
  Subprocess invocation is **argv-style**: a 20k+ character multiline prompt
  is passed to `subprocess.Popen` as one argv element, no shell, no shlex
  round-trip. One complete task = one harness invocation.
- **Request identity, framing, duplicate protection, the persistent
  terminal loop, leases, permissions** — see the detail sections below.

**What it must never own:** DPMtF flows, verdicts, roles, governance,
dispatch, or any bridge database. Nothing in this package imports or queries
a database. It also never owns the model.

## Architecture

```
harness_allocator/
  __init__.py     public API
  __main__.py     python3 -m harness_allocator (persistent terminal)
  config.py       own config surface (env vars + harness-allocator.ini)
  status.py       READY/RUNNING/SUCCESS/ERROR/DUPLICATE_REQUEST tokens
  definition.py   harness identity + environment requirements (no model)
  capabilities.py per-harness capability manifests + the roster
  launchspec.py   LaunchSpec / StopSpec / ResetSpec declarations
  adapter.py      command generation (argv + shell-string forms, per harness)
  transport.py    optional framed transport + request identity
  invoke.py       execute() — argv-style one-shot run + capture + heartbeat
  lease.py        execution leases
  permissions.py  permission surface
  terminal.py     persistent Harness Terminal loop + CLI
  web/            read-only browse UI (FastAPI, port 9142) — the ONE place
                  third-party imports are allowed; the package proper is
                  stdlib-only
```

Architecture decisions against external harness designs are recorded as
ADRs: `docs/ADR-TRUSS-REVIEW.md` holds six concept verdicts
(ADOPT/ADAPT/REJECT/DEFER) from the Truss review, cited to a pinned
read-only clone — a study, not an integration; adopted concepts arrive
through their own governed runs. Archived working notes live under
`docs/archive/`.

## Requirements

- Python 3.10+. **Zero runtime dependencies** for the package proper —
  standard library only.
- The web UI (`harness_allocator.web`) additionally needs `fastapi` and
  `uvicorn`; it is imported only when run.
- The harnesses themselves are external binaries resolved via config/PATH.

## Installation

### Install manually

```bash
git clone https://github.com/svend-blip/harness-allocator.git
cd harness-allocator
pip install -e .          # or just use it in place — stdlib only
python3 -m pytest tests -q
```

### Install using an Agent

Point your coding agent at this repository and ask it to run the manual
steps above; the package has no build step and no dependency resolution to
get wrong. Consumers (e.g. DPMtF) locate the package via the
`HARNESS_ALLOCATOR_PATH` env var or their own project-path config and
import it read-only.

### Verify installation

```bash
python3 -c "import harness_allocator as ha; print(sorted(ha.capabilities.SUPPORTED_HARNESSES))"
python3 -m pytest tests -q     # the full contract suite must be green
```

## Configuration

Reads, in priority order:

1. Environment variables — e.g. `CODEX_BIN`, `DSH_BIN`, `DSH_PROFILE`,
   `QWEN_BIN`, `GOOSE_BIN`, `CRUSH_BIN`, `WHIP_BIN`, `SIMPLE_HARNESS_BIN`,
   `SIMPLE_HARNESS_BASE_URL`, plus per-harness knobs (see
   `harness-allocator.ini` for the full commented catalogue).
2. `harness-allocator.ini` — committed defaults, almost entirely
   commented-out documentation of the surface.
3. Hardcoded conventional fallbacks (the harness's own launcher name).

There is no `.env` loader: credentials come from the process environment, so
a harness inherits them exactly as its own CLI expects. API keys are
configured by **variable name**, never by value.

## Running

```bash
# The persistent terminal (reads framed requests from stdin, Ctrl-D to quit).
python3 -m harness_allocator --role probe --harness dsh \
    --model-target deepseek-v4-pro --cwd .

# One-shot from Python.
python3 -c "from harness_allocator import execute; print(execute(role='probe', harness='dsh', model_target='deepseek-v4-pro', cwd='.', task='echo ok'))"

# Inspect the argv that execute() will pass to subprocess.Popen.
python3 -c "from harness_allocator import build_dsh_argv; print(build_dsh_argv(model_target='deepseek-v4-pro', task='line one\nline two\n'))"

# The read-only browse UI (loopback by default — the UI has no auth;
# widen with HARNESS_WEB_HOST=0.0.0.0 only as an explicit decision).
python3 -m harness_allocator.web        # http://127.0.0.1:9142
```

## Testing

```bash
python3 -m pytest tests -q
```

The suite is the contract: roster-derived (a harness added to
`SUPPORTED_HARNESSES` without manifest/Launch/Stop/ResetSpec entries fails
closed), hermetic (no live harness launches), stdlib + pytest only. The web
UI tests skip cleanly where FastAPI is absent.

## Interface

```python
from harness_allocator import execute

result = execute(role="probe", harness="dsh", model_target="deepseek-v4-pro",
                 cwd=".", task="Summarize this change.")
# -> {"status": "SUCCESS"|"ERROR", "output": str, "error": str,
#     "elapsed": float, "pid": int|None, "request_id": str,
#     "harness": str, "role": str, "model_target": str,
#     "payload_chars": int, "payload_lines": int, "payload_sha256": str}
```

`execute` always returns that shape — on failure the error is reported in
`error`, never raised. `model_target` is the caller's already-resolved target.

## Argv-style subprocess execution

The package uses `subprocess.Popen(argv, ...)` with the complete `task` as one
argv element. There is no shell, no `shlex.split`, no per-line execution. A
20k+ character prompt with hundreds of embedded newlines therefore produces
exactly one harness invocation:

```python
from harness_allocator import build_dsh_argv
argv = build_dsh_argv(model_target="deepseek-v4-pro",
                      task="line one\nline two\nSupervisor\n",
                      cfg=...)
# argv[-1] is the complete task as a single Python string,
# embedded newlines preserved verbatim.
```

The legacy `run_command()` accepts a shell string OR an argv list for backward
compatibility; `run_argv()` is the argv-only form. `execute()` uses `run_argv()`
internally.

## Optional framed transport (terminal stdin)

When callers stream stdin into the persistent terminal, an optional length-
delimited frame protocol is provided so that one complete semantic task maps to
exactly one frame, regardless of how many newlines the payload contains:

```
HAR-FRAME <request_id> <byte_length> [retry]\n
<exactly byte_length bytes of payload>
```

`encode_request(request_id, payload)` produces the frame bytes; the dispatcher
writes them atomically. `FrameReader` reassembles them. A bare single line (no
header) is also accepted as a transitional legacy request.

The optional `retry` token (`encode_request(request_id, payload, retry=True)`)
is the only way to re-execute a completed request identity. Without it, a repeat
of a completed `request_id` + payload hash is reported as `DUPLICATE_REQUEST`
and returns to READY rather than running again.

The framing is implementation detail. Callers who prefer to skip it can call
`execute()` directly with the complete prompt as a Python string.

## Ctrl+C behavior

The persistent terminal handles Ctrl+C the same way, whether it is invoked
directly (`python3 -m harness_allocator`) or through the DPMtF seam
(`scripts/bridgeV002/harness_terminal.py`):

- **READY + Ctrl+C**: the pending input is discarded (`reader.clear()`),
  a short deterministic notice (`[READY] Ctrl+C ignored; terminal remains
  READY.`) is written, and the prompt stays at READY. No harness child
  exists at this point, so nothing else is affected.
- **RUNNING + Ctrl+C**: the terminal's SIGINT handler sets the shared
  `cancel_event` and the active runner terminates the harness child it
  owns. Cancellation is **bounded and staged**:
    1. `SIGINT` to the child's own process group (`start_new_session=True`
       keeps the harness in its own session, isolated from tmux's
       foreground process group and from any unrelated tmux sessions).
    2. After `cancel_grace_seconds * 0.5` (default 0.5s), `SIGTERM`.
    3. After `cancel_grace_seconds` (default 1.0s), `SIGKILL`.
  The pipe drainer is bounded; if a descendant keeps a pipe open after
  the leader is killed, the same isolated group receives a final
  `SIGKILL` and a short additional drain completes the cleanup.
- **Returning to READY**: after a CANCELLED result, the cancel event is
  cleared and the prompt returns to READY. Subsequent submissions run
  cleanly with no leftover state.

The escalation is observable and bounded — there is no orphan harness
process after a successful cancellation. Tests cover this end to end
(`test_run_argv_cancels_long_running_child_without_orphan` and friends).

## Runtime status

`render_banner` / `run_terminal` / `collect_runtime_status` (and the
mirror in `scripts/bridgeV002/harness_terminal.py`) expose the
configuration/runtime fields the Human wants to see at startup:

| Field             | Source                                              | Honest fallback |
|-------------------|-----------------------------------------------------|-----------------|
| Flow              | `--flow` (or `status_info["flow"]`)                 | empty           |
| Role              | `--role`                                            | empty           |
| Harness           | resolved harness key (display label only)           | harness key     |
| Model target      | `--model-target` (already resolved upstream)        | empty           |
| Cwd               | `--cwd` or `os.getcwd()`                            | empty           |
| Sandbox mode      | `DPMTF_SANDBOX_MODE` env                             | `unknown`       |
| Approval policy   | `DPMTF_APPROVAL_POLICY` env                          | `unknown`       |
| Workspace access  | `DPMTF_WORKSPACE_ACCESS_MODE` env                   | `unknown`       |
| Bridge dir        | `DPMTF_BRIDGE_DIR` env **or** explicit `config`     | `not configured`|
| Bridge dir access | `DPMTF_BRIDGE_ACCESS` env or `os.access`            | `unknown`       |
| MCP-Light         | `DPMTF_MCP_LIGHT` env                                | `not configured`|

Values are bounded: any value containing `api_key`, `token`, `secret`,
`password`, or `credential` is filtered to the field's honest default.
Free-form strings are capped at 512 characters. Unknown information is
shown as `unknown` or `not configured` — never guessed.
