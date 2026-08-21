# GOAL — Harness Allocator

Harness Allocator is an independent, optional harness-execution layer for
DPMtF.

## Responsibility boundary

```text
DPMtF
  -> Model Allocator
  -> resolved model_target
  -> Harness Allocator
  -> Harness Adapter
  -> selected harness
```

Harness Allocator does not select or own models. It receives an already
resolved `model_target` from DPMtF.

## Core interface

Conceptually:

```text
execute(role, harness, model_target, cwd, task)
  -> status/output/error/elapsed + operational metadata
```

## What Harness Allocator owns

- harness identity/configuration
- harness adapters / invocation construction
- safe one-shot harness execution
- persistent Harness Terminal lifecycle
- terminal input accumulation/post-processing
- request identity and execution telemetry
- heartbeat / RUNNING / SUCCESS / ERROR / READY states
- duplicate-request protection
- environment requirements specific to the harness execution layer

## What Harness Allocator does not own

- model selection or model lifecycle authority
- DPMtF flows
- DPMtF roles
- DPMtF governance
- handoff sequencing
- verdict handling
- DPMtF bridge database
- job orchestration

## Raw multiline Harness Terminal requirement

Harness Terminal must remain compatible with raw tmux prompt injection.

A multiline prompt may contain arbitrary embedded newlines. Those newlines are
content, not independent submits. Harness Terminal accumulates the complete
submission. When the submission Enter is received, a post-processing step sends
the entire accumulated text to the selected harness as exactly one prompt / one
harness invocation.

For CLI/headless harnesses such as DeepSeek Harness, the complete prompt should
be passed safely as one subprocess argument/value using argv-style invocation
where applicable, not assembled into an unsafe shell command and not executed
line by line.

The implementation must support at least 20k+ characters and hundreds of lines
as one task.

Internal buffering or framing is an implementation detail. Harness Allocator
does not require DPMtF flows to adopt a new external framing protocol.

## Preferred Cloud reference profile

| Role | Harness | Model |
|---|---|---|
| Supervisor | DeepSeek Harness | DeepSeek V4 Pro |
| Implementor | Codex | MiniMax M3 |
| Reviewer | Claude Code | Sonnet 5 |

Model choices remain Model Allocator responsibilities. Harness choices remain
Harness Allocator responsibilities.

## V1 operational requirements

- standalone stdlib-only package
- correct `model_target` boundary
- raw multiline tmux-compatible submission -> one harness invocation
- request id / payload size / line count / stable hash
- heartbeat/progress visibility without chain-of-thought
- deterministic SUCCESS/ERROR -> READY lifecycle
- duplicate-request protection with explicit deliberate retry
- no silent model fallback
- no silent harness fallback
- optional integration: DPMtF continues working without Harness Allocator

## Deferred

- MCP-Light shared harness capability
- automatic harness allocation
- typed verdict/result transport
- parallel harness workers
- ensemble execution
