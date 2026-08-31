# ADR — Truss Architecture Review (Run 022)

> Study of Truss (https://github.com/truss-agent/truss-harness) for
> architectural concepts worth adopting, adapting, rejecting, or deferring
> in the harness-allocator.
>
> **Pin:** `60bbdac` — the exact commit this review is anchored to.
> Every Truss citation below is verified against the read-only clone at
> `/home/svend/truss-harness` checked out at commit
> `60bbdaccfd3d23ad8fb60c07c772c80a47ba3e5c` (short form: `60bbdac`).
>
> **Target:** `harness_allocator/` in `/home/svend/harness-allocator`.
> **No code change.** This Run produces one file and nothing else.

---

## Capability-oriented execution

**What Truss does:** Truss expresses capability as three discrete modes
per agent profile, gated through two coordinated mechanisms:

- `ManagedAgentMode = "chat" | "plan" | "edit"` —
  `packages/runtime/src/agents/contracts.ts:16`. The same union is
  re-exported in `packages/runtime/src/remote.ts:57, 95, 118-119` for
  remote command envelopes.
- **Per-mode system prompt text** constructed by the `systemPrompt()`
  builder in `packages/agent-host/src/host.ts:78-96`. Chat mode (lines
  84-86) tells the model it can inspect but not change; Plan mode
  (lines 87-89) requires a final Markdown checklist and forbids
  changes; Edit mode (line 90-92) instructs the model to act as an
  execution agent and to never claim a file changed unless the write
  tool returned success.
- **Per-mode tool registration** in
  `packages/agent-host/src/host.ts:195-206`. Chat and Plan modes
  register only the read-only workspace tools
  (`read_file_tool`, `list_directory_tool`, `search_files_tool`,
  `grep_tool`) plus optional web tools; Edit mode registers the full
  core toolset via `registerCoreTools()`.
- **Per-mode runtime flags** wired in
  `packages/agent-host/src/host.ts:248-250`:
  `savePlanOnCompletion` (plan), `requireWriteForEditIntent` (edit),
  `deferTextUntilToolDecision` (edit).
- `requireWriteForEditIntent` is declared at
  `packages/runtime/src/agent/contracts.ts:32` and enforced at
  `packages/runtime/src/agent/runtime.ts:199-202` — when the flag is
  set and `hasEditIntent(prompt)` is true but `modifiedFiles.size`
  is zero, the runtime recovers the turn with the edit-policy's
  "no_tools" instruction rather than letting the assistant emit a
  text-only response.

The approval policy (`"ask" | "auto-read" | "auto-all"`,
declared at `packages/runtime/src/agents/contracts.ts:17`) is an
orthogonal dimension; it controls per-tool-call gating, not the mode
itself. Approval logic lives in
`packages/cli/src/protocol/approval.ts:23-34` — `auto-all` short-
circuits to `true`, `auto-read` short-circuits for tools in the
`readOnlyTools` set, otherwise the call blocks on
`pending.set(call.id, { resolve })` until a listener calls
`approve()`/`deny()`.

**What the allocator does today:**
`harness_allocator/permissions.py:31` declares the three normalized
permission modes: `PERMISSION_MODES = ("READ_ONLY", "WORKSPACE_WRITE",
"FULL_ACCESS")`. The manifest per harness declares `workspace.read_only`,
`workspace.workspace_write`, and `workspace.full_access` booleans in
`harness_allocator/capabilities.py` (docstring at line 25; live data
at line 157+). The `effective_permission()` function at
`harness_allocator/permissions.py:64-110` enforces that a requested mode
must be supported by the manifest — `PermissionRefusedError` at line 41
on mismatch, never silent downgrade.

**Delta:** Truss's mode model is orthogonal to DPMtF's permission model.
Truss controls the *system prompt + tool set* available to an agent;
DPMtF controls the *permission envelope* before the harness is even
launched. Truss's three modes map cleanly onto DPMtF's three permission
tiers, but the enforcement mechanism differs: Truss enforces inside the
agent loop via prompt engineering (system prompts and per-mode tool
registration) plus runtime hooks (`requireWriteForEditIntent`); DPMtF
enforces at launch via manifest validation. The allocator does not have
Truss's `requireWriteForEditIntent` — a concept worth adopting as a
runtime-level assertion that write permission was actually exercised
during the run.

Verdict: ADAPT

Adopt `requireWriteForEditIntent` as a new manifest-level assertion:
when a harness runs under `WORKSPACE_WRITE` or `FULL_ACCESS`, verify at
least one write-capable tool was invoked before declaring the run a
success. A follow-up Run would add the verification hook to
`harness_allocator/invoke.py`.

---

## Parallel read-only execution

**What Truss does:** The `AgentCoordinator` in
`packages/runtime/src/agents/coordinator.ts:85` manages concurrent runs
with a configurable `maxConcurrentRuns` parameter (declared at
`coordinator.ts:73`; default 3, validated at
`coordinator.ts:96-102`). Chat and Plan mode runs execute fully in
parallel; they share no workspace state beyond read-only tool access.
The concurrency gate is enforced in `startReadyRuns()` at
`coordinator.ts:258-261`:

```
if (
  run.state !== "queued" ||
  this.runningCount() >= this.maxConcurrentRuns
) {
  continue;
}
```

Read-only agents do not acquire the write lease at all
(`coordinator.ts:264-267`: lease acquisition is gated on
`run.profile.mode === "edit"` only). The coordinator test at
`packages/runtime/src/agents.test.ts:178-185` confirms concurrent
read-only agents run simultaneously and their events are independently
correlated.

**What the allocator does today:** `harness_allocator/lease.py`
implements an exclusive write lease using `fcntl.flock()` on a
per-workspace file. Readers are unrestricted: the `acquire()` function
at `lease.py:142` returns `None` for `READ_ONLY` mode (documented at
`lease.py:166-167` — "When *mode* is `READ_ONLY` — readers are
unlimited"); multiple read sessions proceed concurrently without any
semaphore or count gate. The manifest declares
`concurrency.parallel_readers` per harness in
`harness_allocator/capabilities.py:60` (docstring) and `capabilities.py`
manifest lines (e.g., 181, 226, 271, 317, 372 in the pinned tree), but
the allocator never enforces a concurrency count limit — it relies on
the harness to self-limit.

**Delta:** Truss introduces an explicit concurrency ceiling
(`maxConcurrentRuns`) applied to *all* run slots, while the allocator
has none. The allocator's approach is correct for its threat model
(external harnesses may consume arbitrary resources); Truss's approach
is correct for its threat model (local workspace agent sessions share
one machine with known user intent). The allocator's
`concurrency.parallel_readers` manifest key is a declaration, not an
enforcement — Truss proves the concept can be enforced at the
coordinator level without per-harness logic.

Verdict: DEFER

Truss enforces concurrency at the coordinator level with a single
integer. The allocator would need a similar scheduling layer — the
lease file alone does not bound total resource consumption. A follow-up
Run could add a coordinator-level semaphore to
`harness_allocator/invoke.py` or a dedicated `Scheduler` class. For
now, the allocator's approach (manifest declaration + harness
self-limitation) is acceptable. Reopen if multiple readers from
different harnesses on the same workspace begin interfering in
production.

---

## MCP trust boundaries

**What Truss does:** MCP trust is enforced in two layers. First, the
`GatewayMcpController` interface at
`packages/gateway/src/index.ts:43-45` is strictly read-only:

```
/** Host-owned, read-only MCP state. Secrets and executable configuration stay behind this boundary. */
export interface GatewayMcpController {
  list(): readonly RemoteMcpServerStatus[];
}
```

No remote client can list tools, connect, or invoke MCP servers. The
MCP connections themselves are registered exclusively by the local host
in `packages/agent-host/src/host.ts:218-220` (`registerMcpServers`)
with mode-filtered server lists at `host.ts:208-217`. Per-tool
metadata includes a `readOnly` flag in
`packages/mcp/src/index.ts:26-31` (`McpToolSummary.readOnly` at line
29), and `packages/cli/src/mcp.ts:21` renders it as
`tool.readOnly ? "read-only" : "approval-controlled"` in the CLI status
surface. The approval controller at
`packages/cli/src/protocol/approval.ts:23-34` gates non-read tools
through a `pending.set(call.id, { resolve })` map.

**What the allocator does today:**
`harness_allocator/permissions.py:36` declares the three MCP trust
tiers as `MCP_TRUST_TIERS = ("trusted_system", "trusted_workspace",
"untrusted_workspace")`. The `mcp_tiers_allowed()` function at
`harness_allocator/permissions.py:113` maps permission modes to
allowed tiers. `untrusted_workspace` is never granted execution
rights. MCP is treated as a monolith per tier — no per-tool
granularity.

**Delta:** Truss's MCP boundary is architectural (read-only gateway
interface with no remote modification path); DPMtF's is policy-driven
(tier table mapped from permission mode). Both achieve the same
security outcome but through different mechanisms. Truss's per-tool
`readOnly` classification (`packages/mcp/src/index.ts:29`) could
supplement the allocator's coarse tier approach — the allocator
currently treats all MCP as a monolith per tier. Truss's per-mode
filtering of MCP servers (`packages/agent-host/src/host.ts:208-217`)
is also a concept the allocator's three-tier table does not encode.

Verdict: ADOPT

Adopt the per-tool `readOnly` classification pattern as a supplement
to the existing three-tier table. A follow-up Run could add
`mcp_tool_read_only` to the harness manifest schema in
`harness_allocator/capabilities.py` and filter MCP tier assignments at
the per-tool level. This is a backward-compatible addition — the tier
table remains authoritative for bulk decisions.

---

## Workspace-local agent configuration

**What Truss does:** Truss stores all agent configuration (profiles,
run history, plans) in a workspace-local directory: `.truss-harness/`
under the workspace root. Constants and path helpers live in
`packages/cli/src/agents.ts`:

- `profilesFileName = "agents.json"` at `packages/cli/src/agents.ts:20`
- `agentProfilesPath(workspaceRoot)` at `packages/cli/src/agents.ts:23-25`
- `runHistoryFileName = "runs.json"` at `packages/cli/src/agents.ts:21`
- `agentRunHistoryPath(workspaceRoot)` at `packages/cli/src/agents.ts:27-28`

The configuration schema at `packages/cli/src/config.ts:24-43`
includes `provider`, `baseUrl`, `model`, `credentialRef`, `mode`,
`permission`, `internetAccess`, `systemPrompt`, `apiKeyEnv`,
`mcpServers`, and `tuiTheme` (and the `HarnessConfiguration`
extension at line 38 with `defaultProfile`, `profiles`,
`allowWorkspaceMcpServers`). Credentials are never stored on disk —
`credentialRef` (declared at `packages/cli/src/config.ts:31-32` as
`apiKeyEnv`) is an opaque reference resolved from environment
variables or host-client secure storage. MCP server configs are
workspace-local JSON shapes declared at `packages/mcp/src/index.ts:7-13`.

The host layer that bridges CLI to runtime is the `HostedRuntime`
interface at `packages/agent-host/src/host.ts:55-76` (and the
constructing `AgentHost` class at lines 114+). The CLI bin at
`packages/cli/src/bin.ts:315` passes `workspaceRoot: cwd()` to
`executeWorkspaceCommand`, and `bin.ts:576-577` scans all configured
workspace roots with `workspaceRoots.map(...)` to build one
`createCliAgentCoordinator` instance per workspace.

**What the allocator does today:** The allocator has no concept of
workspace-local agent configuration. Configuration is external to
the harness — provided via the DPMtF flow dispatch mechanism (role
files, governance documents, launch args). The harness is stateless
between invocations: `harness_allocator/invoke.py` receives
everything as function parameters and returns a result dict. There
is no profile store, no run history, no local credential management.

**Delta:** Truss's workspace-local configuration is a client-side
pattern for persistent agent profiles. The allocator's architecture
(stateless invocation through the DPMtF orchestrator) serves a
different purpose: the allocator is a tool launcher, not an agent
development environment. Truss's pattern would not translate cleanly
— the allocator's strength is its ability to swap harnesses mid-run
based on DPMtF policy decisions. However, the credential-separation
principle (`apiKeyEnv` resolves to an env var rather than a raw
secret string) is a pattern worth noting for future allocator
hardening.

Verdict: DEFER

Truss's workspace-local profiles and run history serve a client-side
agent development workflow. The allocator's stateless invocation
model is deliberately orthogonal. The credential-separation
pattern (`apiKeyEnv` → env var resolution) could be adopted as a
small follow-up to improve `harness_allocator/definition.py`'s env
handling. Reopen if a follow-up Run on `definition.py` introduces
an env-handling pass.

---

## Agent/runtime separation

**What Truss does:** Truss separates the agent lifecycle (managed by
`AgentCoordinator` in `packages/runtime/src/agents/coordinator.ts:85`)
from the runtime execution (the `AgentRuntime` loop in
`packages/runtime/src/agent/runtime.ts:28`). The coordinator owns
scheduling, queueing, write-lease management, and run state
transitions; the runtime owns the iterative LLM loop, session
management, and tool execution.

The factory pattern (`packages/runtime/src/agents/contracts.ts:181-184`)
decouples creation from scheduling:

```
export interface AgentRuntimeFactory {
  validate(profile: AgentProfile): Promise<void>;
  create(profile: AgentProfile): Promise<CreatedManagedAgentRuntime>;
}
```

`CreatedManagedAgentRuntime` at `contracts.ts:171-178` holds the
runtime, an event subscription handle, and an optional approval
controller — giving the coordinator full lifecycle control without
knowing runtime internals. The `AgentHost` in
`packages/agent-host/src/host.ts:114+` is the bridge layer between
the CLI and the runtime — it resolves credentials, creates the
`AgentCoordinator` (line 184+ via `createRuntimeWithProvider`), and
returns a `HostedRuntime`.

**What the allocator does today:**
`harness_allocator/invoke.py` is a monolithic invocation function: it
builds argv, launches the subprocess, manages cancellation, and
returns the result. There is no coordinator, no scheduler, no factory
pattern — the harness process is the entire runtime. The adapter
(`harness_allocator/adapter.py`) builds the launch command but does
not manage the process lifecycle beyond returning the argv list.
The launch/stop lifecycle is owned by `harness_allocator/launchspec.py`
(`LaunchSpec` and `StopSpec`), which is a *declarative* separation —
it describes how to start and stop, but does not execute the
coordination.

**Delta:** Truss's separation is operational (a running coordinator
manages multiple agent runtimes); the allocator's is declarative
(spec documents describe how to start/stop). Truss's factory pattern
(`AgentRuntimeFactory`) is a clean contract that the allocator could
adopt for its adapter layer: instead of monolithic `build_*_argv`
functions, each harness would have a `LaunchFactory` interface that
produces launch commands, stop commands, and cancellation behavior
as discrete, testable objects.

Verdict: ADAPT

Adopt the factory pattern for harness launch/stop/cancel. A
follow-up Run would introduce a `HarnessFactory` interface in
`harness_allocator/adapter.py` that replaces the current monolithic
`build_*` functions. Each harness adapter would implement:
`build_launch_args()`, `build_stop_args()`, `build_cancel_signal()`.
This improves testability (no subprocess needed for argv unit
tests) and makes it easier to add new harnesses without touching
invoke logic.

---

## Concurrency semantics

**What Truss does:** Concurrency is governed by three independent
mechanisms in the `AgentCoordinator` in
`packages/runtime/src/agents/coordinator.ts:85`:

1. **Max concurrent runs:** `maxConcurrentRuns` (declared at
   `coordinator.ts:73`; default 3, validated at
   `coordinator.ts:96-102`). Enforced in `startReadyRuns()` at
   `coordinator.ts:258-261` — runs that would exceed the limit
   enter `"queued"` state and wait for a slot.

2. **Write lease (exclusive writer):** Edit-mode runs acquire an
   exclusive `WorkspaceWriteLease` before starting
   (`coordinator.ts:264-267`). The lease interface at
   `packages/runtime/src/agents/write-lease.ts:3-8` is documented as
   substitutable:

   ```
   /** One holder at a time; a host may replace this with a durable or distributed lease. */
   export interface WorkspaceWriteLease {
     holder(): AgentRunId | undefined;
     tryAcquire(runId: AgentRunId): boolean;
     release(runId: AgentRunId): boolean;
   }
   ```

   The default implementation (`InMemoryWorkspaceWriteLease` at
   `write-lease.ts:10-27`) keeps the holder in a single `heldBy`
   field.

3. **Per-agent serialization:** `runsByAgent` map at
   `coordinator.ts:88` (declared `private readonly runsByAgent =
   new Map<AgentId, AgentRunId>()`) — one profile has at most one
   queued or active run. The coordinator throws at
   `coordinator.ts:180-184` if a second start is attempted:
   `"This agent already has an active or queued run."`

These three mechanisms form a layered concurrency model: a
concurrency count limit for all runs, an exclusive write lease for
Edit mode, and one-active-run-per-profile serialization.

**What the allocator does today:** `harness_allocator/lease.py`
implements an exclusive write lease using `fcntl.flock()` — a
file-level OS lock. The `Lease` dataclass at
`harness_allocator/lease.py:55-65` carries `workspace`,
`request_id`, `role`, `harness`, `acquired_at`, and `pid`. The
`acquire()` function at `lease.py:142` supports three conflict
modes (REJECT / WAIT, with REJECT as default — see the
`conflict_behaviour` parameter documented near line 162). Stale
lease recovery is handled by `recover_stale()` which checks pid
liveness and lease age.

Readers are unbounded — `acquire()` returns `None` for
`READ_ONLY` mode (documented at `lease.py:166-167`). There is no
per-role or per-harness serialization gate; the allocator relies
on the DPMtF orchestrator (outside this package) to schedule
dispatches.

**Delta:** The allocator's lease mechanism is more mature than
Truss's in one dimension: it uses an OS-level file lock
(`fcntl.flock`) that survives process crashes and can be detected
across multiple allocator instances. Truss's
`InMemoryWorkspaceWriteLease` is ephemeral — if the process dies,
the lease is lost. However, Truss's lease interface is explicitly
designed to be replaced with a durable implementation (per the
docstring at `write-lease.ts:3`).

The allocator also lacks Truss's per-agent (per-role)
serialization: the allocator's DPMtF orchestrator handles this
externally, but the lease module itself does not enforce it. The
`Lease` dataclass at `lease.py:60-65` already carries `role`, but
`acquire()` at `lease.py:142` does not use `role` as a
serialization key.

Truss's three-layer model (count limit + exclusive write +
per-agent serialization) could inform a future allocator refactor
where the lease module gains a per-role serialization gate,
making concurrency semantics visible in the allocator's own code
rather than delegated entirely to the DPMtF dispatch layer.

Verdict: ADAPT

Adopt the per-role serialization gate as a lease-level assertion.
The `Lease` dataclass at `harness_allocator/lease.py:55-65` already
carries `role` — the `acquire()` function at `lease.py:142` could
reject a second acquire from the same role on the same workspace
(even if the pid differs), mirroring Truss's `runsByAgent`
behavior at `coordinator.ts:88, 180-184`. This would catch
stale-role invocations that slip past the pid-based stale
detection. A follow-up Run would add a `per_role_serialize`
parameter to `harness_allocator/lease.py` near line 142.

---

*This ADR is a study artifact — it does not change any code. Each
ADOPT/ADAPT finding names a follow-up Run shape. DEFER entries
name the conditions under which the concept should be reconsidered.
REJECT entries (none in this run) would name the cost that
outweighed the benefit.*

*Pin verification: all Truss file paths verified against*
`/home/svend/truss-harness` *at commit* `60bbdac` *(full SHA
`60bbdaccfd3d23ad8fb60c07c772c80a47ba3e5c`)*. *Allocator citations
verified against the working tree at*
`/home/svend/harness-allocator`.
