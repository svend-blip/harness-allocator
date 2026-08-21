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

## Model boundary

Harness Allocator does **not** resolve, select, replace, or own the model.
Model Allocator resolves the model target first; DPMtF passes the already-resolved
`model_target` here, and the allocator only renders it into the harness's native
CLI. There is no `resolve_model()` and no silent model or harness substitution.

## What it owns

- **Identity resolution** — `resolve_harness`, `resolve_role_key`,
  `HarnessDefinition.from_role` (role → harness key). No model.
- **Command generation** — `build_launch_command`, `build_launch_argv`,
  `build_dsh_invocation`, `build_dsh_argv`, `build_task_invocation`,
  `build_task_argv` (the DeepSeek Harness and Codex launch surfaces),
  rendering a passed-through `model_target`.
- **One-shot execution** — `execute(role, harness, model_target, cwd, task)`
  returning `{ status, output, error, elapsed, pid, request_id, ... }`.
  Subprocess invocation is **argv-style**: a 20k+ character multiline prompt
  is passed to `subprocess.Popen` as one argv element, no shell, no shlex
  round-trip. One complete task = one harness invocation.
- **Optional framing** — `transport.py` (`encode_request`, `extract_frame`,
  `FrameReader`): a length-delimited frame protocol is provided for callers
  that stream stdin into the persistent terminal. It is an implementation
  detail, not a binding DPMtF contract; callers may feed `execute()` directly
  with a complete Python string instead.
- **Request identity / payload verification** — `compute_identity` returns
  `request_id`, `chars`, `lines`, `sha256` (execution metadata, never
  chain-of-thought).
- **The persistent terminal loop** — `render_banner` + `run_terminal` + `main`
  (`python3 -m harness_allocator`): banner → atomic request → execute → READY,
  with RUNNING/HEARTBEAT/SUCCESS/ERROR progress and no private reasoning.
- **Duplicate-request protection** — a completed `(request_id, payload sha256)`
  is recorded and never executed twice: a repeat reports `DUPLICATE_REQUEST`
  and returns to READY, unless the frame carries an explicit `retry` flag.
- **Readiness/status state** — `READY` / `RUNNING` / `SUCCESS` / `ERROR` /
  `DUPLICATE_REQUEST`.
- **Environment requirements** — `REQUIRED_ENV`, `missing_env`,
  `describe_missing`.

## What it must never own

DPMtF flows, verdicts, roles, governance, dispatch, or any bridge database.
Nothing in this package imports or queries a database. It also never owns the
model: no resolution, no selection, no silent substitution.

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

## Ctrl+C behavior (Run 002)

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
process after a successful cancellation. Tests cover this end to end:

- `test_run_argv_cancels_long_running_child_without_orphan` proves the
  PID is gone (no `/proc/<pid>`).
- `test_run_argv_cancels_returns_cancelled_status_token` proves the
  `CANCELLED` token is reported, not `SUCCESS`.
- `test_run_terminal_running_cancel_returns_to_ready` proves the
  persistent loop returns to READY after the cancellation.
- `test_run_terminal_cancelled_turn_clears_event_for_next_turn` proves
  the next submitted turn runs cleanly without a re-immediate cancel.

## Runtime status (Run 002)

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

## Layout

```
harness_allocator/
  __init__.py   public API
  __main__.py   python3 -m harness_allocator (persistent terminal)
  config.py     own config surface (env vars + harness-allocator.ini)
  status.py     READY/RUNNING/SUCCESS/ERROR/DUPLICATE_REQUEST tokens
  definition.py harness identity + environment requirements (no model)
  adapter.py    command generation (argv + shell-string forms)
  transport.py  optional framed transport + request identity
  invoke.py     execute() — argv-style one-shot run + capture + heartbeat
  terminal.py   persistent Harness Terminal loop + CLI
```

## Configuration

Reads, in priority order:

1. Environment variables — `CODEX_BIN`, `DSH_BIN`, `DSH_PROFILE`,
   `DSH_V4_PRO_PATCH`.
2. `harness-allocator.ini` (`[harness]` section) — committed defaults.
3. Hardcoded fallbacks (`codex`, `npx @deepseek-ai/dsh`, `headless`, empty patch).

There is no `.env` loader: credentials come from the process environment, so a
harness inherits them exactly as its own CLI expects.

## Requirements

Python 3.10+. **Zero runtime dependencies** — standard library only.

## Run the tests

```bash
python3 -m pytest tests -q
```

## Try it

```bash
# Run the persistent terminal (reads framed requests from stdin, Ctrl-D to quit).
python3 -m harness_allocator --role probe --harness dsh \
    --model-target deepseek-v4-pro --cwd .

# Or one-shot from Python.
python3 -c "from harness_allocator import execute; print(execute(role='probe', harness='dsh', model_target='deepseek-v4-pro', cwd='.', task='echo ok'))"

# Inspect the argv that execute() will pass to subprocess.Popen.
python3 -c "from harness_allocator import build_dsh_argv; print(build_dsh_argv(model_target='deepseek-v4-pro', task='line one\nline two\n'))"

# Encode a frame for dispatch (optional, for stdin injection).
python3 -c "from harness_allocator import encode_request; import sys; sys.stdout.buffer.write(encode_request('ha-1', 'line one\nline two\n'))"
```
