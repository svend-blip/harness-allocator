# Harness Allocator — Recommended Roadmap Extension

## 1. Direction

The Harness Allocator should remain a **thin, optional execution abstraction layer** between DPMtF and coding-agent harnesses.

It MUST NOT become a second orchestrator.

Responsibility boundaries:

```text
DPMtF
  owns:
    workflow
    roles
    sequencing
    parallelism
    verdict handling
    governance
    task lifecycle

        │
        ▼

Harness Allocator
  owns:
    harness selection
    harness capability discovery
    adapter normalization
    invocation
    permission mapping
    session handling
    normalized execution results

        │
        ├── Claude Code
        ├── Codex CLI
        ├── OpenCode
        ├── Pi
        ├── DeepSeek Harness
        ├── Qwen Code
        ├── Goose
        ├── SWE-agent
        ├── Aider
        └── future harnesses

        │
        ▼

Model Allocator
  owns:
    model selection
    provider/runtime selection
    local/cloud routing
    llama.cpp lifecycle
    remote runtime lifecycle
    model availability
```

DPMtF MUST continue to work when Harness Allocator is disabled or unavailable.

Harness Allocator therefore remains an optional capability, not a mandatory DPMtF dependency.

---

# 2. Primary Architectural Goal

Move from:

```text
role -> specific harness
```

toward:

```text
role
  -> required capabilities
  -> Harness Allocator
  -> compatible harness
```

DPMtF should not need detailed knowledge of every supported harness.

Example:

```yaml
role: imple01

harness_requirements:
  terminal: true
  headless: true
  workspace_write: true
  session_resume: true
  skills: preferred
  mcp: preferred
```

Harness Allocator can then determine which configured harnesses satisfy the requirements.

Explicit harness selection MUST remain supported.

Example:

```yaml
harness: codex
```

This preserves deterministic operation when required.

---

# 3. Introduce a Harness Capability Model

Each adapter should expose a normalized capability manifest.

Suggested initial capabilities:

```yaml
capabilities:

  execution:
    terminal: true
    headless: true
    interactive: true

  workspace:
    read_only: true
    workspace_write: true
    full_access: false

  sessions:
    persistent_session: true
    session_resume: true

  extensions:
    skills: true
    mcp: true
    custom_tools: false

  models:
    local_models: true
    cloud_models: true
    external_model_allocator: true

  workflow:
    structured_output: true
    patch_output: true
    git_aware: true

  automation:
    non_interactive: true
    deterministic_exit: true
```

Do not attempt to model every harness-specific feature.

Only add capabilities when DPMtF actually needs to make a routing or governance decision based on them.

---

# 4. Normalize Invocation

Introduce a stable internal request object independent of individual harness CLIs.

Conceptually:

```text
HarnessRunSpec
```

Suggested fields:

```yaml
request_id:
role:
harness:
working_directory:
prompt:
model_reference:

permissions:
  mode:

session:
  mode:
  session_id:

requirements:
  capabilities: []

environment: {}

timeout:

output:
  expected_artifacts: []
```

Harness-specific CLI arguments MUST remain inside adapters.

DPMtF must not construct commands such as:

```text
codex ...
claude ...
opencode ...
qwen ...
```

directly when execution is routed through Harness Allocator.

---

# 5. Normalize Results

All adapters should return a common execution result.

Conceptually:

```text
HarnessRunResult
```

Minimum fields:

```yaml
request_id:
harness:
status:
exit_code:

session_id:

stdout:
stderr:

artifacts: []

evidence:
  changed_files: []
  patch_available:
  tests_run: []

usage:
  input_tokens:
  output_tokens:
  cost:

timing:
  started_at:
  finished_at:
```

Fields unsupported by a harness may remain null.

The purpose is normalization, not forcing every harness to expose identical telemetry.

---

# 6. Permission Normalization

Permission state should become a first-class Harness Allocator concept.

Initial normalized modes:

```text
READ_ONLY
WORKSPACE_WRITE
FULL_ACCESS
```

Each adapter maps these modes to the closest native harness mechanism.

The Harness Terminal should always display the effective permission state.

Example:

```text
Harness: Codex CLI
Role: imple01
Access: WORKSPACE_WRITE
Model: minimax-m3
Session: active
```

If the requested permission cannot be enforced by a harness, Harness Allocator MUST report that explicitly rather than silently approximating it.

---

# 7. Improve Harness Terminal

The Harness Terminal should remain lightweight but become operationally useful.

Add:

### Execution status

Examples:

```text
STARTING
READY
RUNNING
WAITING
COMPLETED
FAILED
INTERRUPTED
```

### Visible execution identity

Show:

```text
role
harness
model
permission mode
workspace
request ID
```

### Large paste support

Support large multi-line prompt submission without requiring line-by-line terminal input.

This is especially important for browser-oriented harnesses exposed through the terminal bridge.

### Interrupt behavior

Explicitly test and document:

```text
CTRL+C
```

Distinguish between:

```text
cancel current harness task
```

and:

```text
terminate Harness Terminal
```

Accidental destruction of the surrounding tmux execution chain should be prevented where practical.

---

# 8. Skills Support

Introduce an optional normalized skill invocation mechanism.

Example:

```text
/skill cold-start
```

The adapter determines the harness-specific implementation.

Possible mappings:

```text
Claude Code    -> native skill
Codex          -> Codex-compatible instruction/skill
OpenCode       -> configured skill
Pi             -> Pi skill
Qwen Code      -> adapter implementation
other harness  -> prompt injection fallback
```

A fallback implementation MAY inject skill content into the task context when native skill support does not exist.

However, Harness Allocator should expose whether the skill was:

```text
NATIVE
EMULATED
UNSUPPORTED
```

This prevents DPMtF from assuming equivalent semantics.

---

# 9. Next Reference Adapters

Do not attempt broad harness collection.

Add a small number of adapters specifically to validate the abstraction.

## Qwen Code

Priority: HIGH

Purpose:

Validate a terminal-oriented harness closely associated with models that may run locally through DPMtF infrastructure.

Target architecture:

```text
DPMtF
  -> Harness Allocator
      -> Qwen Code
          -> Model Allocator
              -> llama.cpp
                  -> local Qwen
```

Success would provide strong evidence that harness selection and model selection are genuinely independent.

---

## Goose

Priority: HIGH

Purpose:

Validate a model-agnostic extensible agent harness.

Goose should test whether Harness Allocator can integrate a substantially different harness without introducing DPMtF-specific code.

---

## SWE-agent

Priority: MEDIUM / EXPERIMENTAL

Treat SWE-agent initially as a specialized capability rather than a general replacement for imple roles.

Potential capability:

```text
repo_task_agent
```

Example future use:

```text
Supervisor
    ↓
SWE-agent
    ↓
patch + test evidence
    ↓
Reviewer
```

Do not alter existing DPMtF flows to accommodate SWE-agent.

---

## Aider

Priority: MEDIUM / EXPERIMENTAL

Evaluate primarily as a specialized Git/patch integration harness.

Potential capabilities:

```text
git_aware
patch_output
patch_application
```

This may later complement the proposed DPMtF Patcher role.

Do not make Aider a required component of Patcher.

---

## Crush

Priority: LOW / COMPATIBILITY TEST

Crush is useful primarily as an abstraction test.

The goal is not production adoption.

The test question is:

> Can a new terminal coding harness be added through one isolated adapter without modifying DPMtF orchestration logic?

If yes, the Harness Adapter Interface is working as intended.

---

# 10. Adapter Contract

Every harness integration should implement the same minimal lifecycle.

Conceptually:

```text
probe()
capabilities()
prepare()
start()
send()
status()
interrupt()
collect()
resume()
cleanup()
```

Not every harness must implement every operation natively.

Unsupported operations MUST be explicitly reported.

Adapters should contain harness-specific behavior.

Core Harness Allocator code should not accumulate:

```python
if harness == "claude":
...
elif harness == "codex":
...
elif harness == "qwen":
...
```

for normal runtime behavior.

---

# 11. Harness Discovery and Health

Add lightweight probing.

Example:

```text
harness-allocator list
```

Possible result:

```text
HARNESS         STATUS       VERSION       MODE
claude-code     READY        ...           terminal
codex           READY        ...           terminal
opencode        READY        ...           terminal
pi              READY        ...           terminal
deepseek        READY        ...           browser
qwen-code       READY        ...           terminal
goose           MISSING      -             terminal
```

Also provide:

```text
harness-allocator doctor
```

It should detect:

```text
binary missing
unsupported version
configuration missing
model endpoint unavailable
permission incompatibility
adapter error
```

Do not automatically install or modify third-party harnesses.

---

# 12. Harness Selection

Support two modes.

## Explicit

```yaml
harness: codex
```

This remains the default deterministic mechanism.

## Capability-based

Optional later extension:

```yaml
harness: auto

requires:
  terminal: true
  workspace_write: true
  local_models: true
```

Harness Allocator returns compatible candidates according to configuration.

Initially, automatic selection SHOULD be conservative.

Do not introduce AI-based harness selection.

Use deterministic rules.

---

# 13. Keep Model Allocation Separate

Harness Allocator MUST NOT become responsible for deciding which LLM should execute a role.

Example:

```text
role
 ↓
DPMtF
 ↓
Harness Allocator
 ↓
Codex
 ↓
Model Allocator
 ↓
MiniMax M3
```

The harness may impose model compatibility constraints, but model lifecycle and provider selection remain Model Allocator responsibilities.

This boundary is important for future combinations such as:

```text
Qwen Code + local Qwen
Codex + MiniMax
Claude Code + Anthropic
OpenCode + local llama.cpp
Pi + local llama.cpp
```

without multiplying DPMtF configurations.

---

# 14. Preserve Direct Execution

Existing DPMtF execution MUST remain available.

Conceptually:

```yaml
execution:
  harness_allocator: disabled
```

should preserve the existing path.

And:

```yaml
execution:
  harness_allocator: enabled
```

should enable harness abstraction.

This makes migration incremental and reversible.

---

# 15. Testing Strategy

Harness Allocator should include contract tests that every adapter must pass.

Minimum tests:

```text
probe harness
report capabilities
start execution
submit prompt
capture output
detect completion
capture exit status
interrupt execution
permission mapping
large multi-line input
session cleanup
failure propagation
```

Where supported:

```text
resume session
skill invocation
MCP availability
patch collection
token/cost telemetry
```

Add one important portability test:

> Adding a new simple terminal harness must require only a new adapter, configuration, and tests — no DPMtF orchestration changes.

This should become an architectural acceptance criterion.

---

# 16. Suggested Implementation Sequence

## Phase HA-1 — Stabilize Existing Harnesses

Finish the common adapter contract around the currently supported harnesses.

Add:

```text
capability manifest
normalized permissions
normalized RunSpec
normalized RunResult
status reporting
large paste support
CTRL+C semantics
```

Do not add new harnesses before this contract is stable.

---

## Phase HA-2 — Qwen Code Adapter

Implement Qwen Code as the first new reference adapter.

Primary test:

```text
DPMtF
 -> Harness Allocator
 -> Qwen Code
 -> local model endpoint
```

No DPMtF orchestration modifications should be required.

---

## Phase HA-3 — Goose Adapter

Implement Goose.

Use it to test model-agnostic harness behavior and validate the capability abstraction.

Again, DPMtF should require no harness-specific changes.

---

## Phase HA-4 — Specialized Harness Experiment

Add experimental adapters for:

```text
SWE-agent
Aider
```

Do not expose them as normal production defaults initially.

Determine whether specialized capabilities such as:

```text
repo_task_agent
patch_output
git_aware
```

are useful enough to formalize.

---

## Phase HA-5 — Portability Test

Implement a minimal Crush adapter.

Treat this as an architectural test rather than a feature.

Acceptance criterion:

```text
new adapter
+ config
+ tests
```

must be sufficient.

If core DPMtF changes are required, revisit the abstraction.

---

# 17. Reference Existing Harness Abstraction Projects

Before freezing the Harness Adapter Interface, study existing multi-harness abstraction projects such as `twaldin/harness`.

Do NOT introduce such a project as a mandatory dependency at this stage.

Instead, compare concepts including:

```text
normalized RunSpec
normalized RunResult
adapter lifecycle
process management
capability reporting
error normalization
usage reporting
```

Reuse good architectural ideas where appropriate while keeping Harness Allocator under DPMtF's own governance.

---

# 18. Non-Goals

Harness Allocator should NOT become:

```text
a workflow engine
an autonomous supervisor
a multi-agent planner
a verdict engine
a model router
a replacement for Model Allocator
a replacement for DPMtF
a universal abstraction over every coding agent
```

Avoid supporting harnesses simply because they exist.

A new harness should be added only when it:

1. provides a useful capability,
2. validates an architectural assumption,
3. provides a meaningful execution alternative,
4. or is required by a DPMtF workflow.

---

# 19. Long-Term Architecture

The desired architecture is:

```text
                    DPMtF
                      │
          workflow / role / governance
                      │
                      ▼
              Harness Allocator
                      │
           capability + adapter layer
                      │
       ┌──────────────┼──────────────┐
       ▼              ▼              ▼
     Codex        Claude Code     Qwen Code
       │              │              │
       ├──────────────┼──────────────┤
                      ▼
                Model Allocator
                      │
       ┌──────────────┼──────────────┐
       ▼              ▼              ▼
   llama.cpp       Cloud API      Remote Node
       │
       ▼
   Local Model
```

Specialized execution may later coexist:

```text
Harness Allocator
       │
       ├── General coding harness
       │
       ├── Repository agent
       │
       └── Patch/Git agent
```

without changing DPMtF's fundamental orchestration model.

---

# 20. Definition of Success

Harness Allocator is successful when:

* DPMtF can run unchanged without it.
* DPMtF can use multiple harnesses through one stable interface.
* Harness-specific behavior remains isolated inside adapters.
* Model allocation remains independent from harness allocation.
* Permission state is explicit and observable.
* Terminal execution is robust enough for autonomous chains.
* Local and cloud models can be combined with compatible harnesses.
* New harnesses can be added without modifying DPMtF orchestration.
* Specialized agents can be introduced without redefining normal roles.
* Harness diversity reduces vendor lock-in rather than increasing system complexity.

The objective is therefore not:

> Support every coding harness.

The objective is:

> Make the harness replaceable without making DPMtF aware of how each harness works.
