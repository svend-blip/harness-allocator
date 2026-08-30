"""Normalized capability manifest for the supported harnesses (eleven total in
this module — ``codex``, ``claude-code``, ``opencode``, ``dsh``, ``qwen``,
``goose``, ``crush``, ``whip``, ``simple-harness`` are the NINE ``SUPPORTED_HARNESSES``; ``sweagent`` and
``aider`` are the TWO ``EXPERIMENTAL_HARNESSES`` — registered in the
adapter surface but gated by ``[experimental] enabled_harnesses``, NOT
exposed as defaults). ``goose`` (Run 026 / HA-3) was the first EXTENSIBLE
harness in the set; ``sweagent`` (Run 027 / HA-4) was the first SPECIALIZED
"repo-task-agent" harness in the set; ``aider`` (Run 027 / HA-4) was the
first SPECIALIZED "git-aware + patch-output" harness in the set;
``crush`` (Run 028 / HA-5) is a chat-style one-shot with Skills + MCP
support and no repo-task / git-aware / patch-output specialization
(skills/mcp True, custom_tools False; the three specialized keys are
False, PRESENT-and-False per GOAL.md §3b). ``SUPPORTED_HARNESSES`` is the production set (the
default scope of ``get_capabilities``); ``EXPERIMENTAL_HARNESSES`` is the
CARVE-OUT — registered in the adapter surface but NOT exposed as defaults,
gated by ``[experimental] enabled_harnesses`` (env
``EXPERIMENTAL_ENABLED_HARNESSES``).

The manifest is a dict with EXACTLY five groups (the ROADMAP §3 normalized
shape MINUS its speculative ``models`` and ``workflow`` groups, which this run
deliberately drops — GOAL.md Run 020 §1 D1). The fields in each group are
listed below; no field or group is added beyond these.

    execution:    terminal (bool), headless (bool), interactive (bool)
    workspace:    read_only (bool), workspace_write (bool), full_access (bool)
    sessions:     persistent_session (bool), session_resume (bool), mode (str)
    extensions:   skills (bool), mcp (bool), custom_tools (bool),
                  repo_task_agent (bool), git_aware (bool),
                  patch_output (bool)

                  The last three are the SPECIALIZED keys, formalized by
                  Human decision at the HA-4 exit (2026-08-25) on the run-027
                  D5 assessment. They are UNIVERSAL: every harness declares
                  all three, and a harness that lacks the capability declares
                  it False rather than omitting the key.

                  Universal because a consumer choosing between a chat-style
                  harness and a repo-task or patch-emitting one has to be able
                  to ASK. An optional key answers "absent", which the caller
                  must then decide how to read — and "absent means false" is a
                  reading this project has already had falsified once.

                  ROADMAP §3's rule ("only add capabilities when DPMtF
                  actually needs to make a routing or governance decision
                  based on them") governs WHETHER a key exists, not whether
                  it may be missing where false. These three earned their
                  place by being measurable in the adapters' argv and env
                  envelopes; declaring them everywhere is what makes them
                  usable.

                  Note a divergence worth knowing: ROADMAP §3's own example
                  places patch_output and git_aware under a ``workflow``
                  group. This manifest is bound to EXACTLY five groups
                  (Run 020 §1 D1), so they live in ``extensions``. Adding a
                  sixth group would break a contract every existing test
                  asserts; the grouping is the compromise, the keys are not.
    automation:   non_interactive (bool), deterministic_exit (bool),
                  interrupt_safe (bool)

    concurrency:  parallel_readers (bool), exclusive_workspace_writer (bool)

    lifecycle:    interrupt_current_task (bool), resumable_session (bool),
                  child_process_cleanup (bool)

    models:       openai_compatible_endpoint (bool)

Values are grounded in measured behavior of the existing package
(``harness_allocator/adapter.py``, ``harness_allocator/invoke.py``,
``harness_allocator/definition.py``, ``harness_allocator/terminal.py``):

- codex: resident TUI launched by ``adapter._codex_argv``
  (``codex -m <model> -C ... --sandbox ... --ask-for-approval ...``); one-shot
  form rejected by ``adapter.build_task_argv`` ("no one-shot task invocation
  for harness 'codex'"); MCP via ``adapter.build_codex_mcp_setup_argv``
  (``codex mcp add <name> --url <url>``).
- claude-code / opencode: resident interactive TUI coding clients (the package
  does NOT build their launch argv — they are DPMtF-launched per
  ``terminal.HARNESS_LABELS``); manifest shape identical to codex except
  ``sessions.mode = "resident"``.
- dsh: headless one-shot invoked by ``adapter.build_dsh_argv``
  (``dsh --profile <p> [--patch <p>] [task]``); MCP via
  ``adapter.build_dsh_mcp_patch_yml`` (DSH MCP patch overlay); the measured
  SIGINT → SIGTERM → SIGKILL process-group cancel path lives in
  ``invoke.run_argv`` (CANCEL_GRACE_SECONDS = 1.0,
  _CANCEL_TERM_ESCALATION_FRACTION = 0.5).

``sessions.mode`` values are BOUND (non-negotiable):
    codex        → "fresh"
    claude-code  → "resident"
    opencode     → "resident"
    dsh          → "oneshot"

The four typed errors are :class:`ValueError` subclasses (the package's idiom —
legacy ``except ValueError`` callers keep working). They are bound by name so
later handoffs (``runspec.py`` RunSpec validation, ``invoke.execute_spec``
capability refusal) can import them from here:

    UnknownHarnessError        — raised by ``get_capabilities`` for any harness
                                 key not in ('codex', 'claude-code', 'opencode',
                                 'dsh', 'qwen'). USED IN THIS HANDOFF.
    MissingPromptError         — reserved for RunSpec validation (handoff 2).
    UnsupportedCapabilityError — reserved for execute_spec capability refusal
                                 (handoff 3).
    LargeInputRefusedError     — reserved for execute_spec large-input refusal
                                 (handoff 3).

This module is stdlib-only (no new imports).
"""

from __future__ import annotations


# ── Typed errors (ValueError subclasses — package idiom) ───────────────


class UnknownHarnessError(ValueError):
    """Raised by ``get_capabilities`` for a harness key that is not one of
    the nine supported harnesses (``codex``, ``claude-code``, ``opencode``,
    ``dsh``, ``qwen``, ``goose``, ``crush``, ``whip``, ``simple-harness``). The message names the unknown harness.
    """


class MissingPromptError(ValueError):
    """Reserved for RunSpec validation (handoff 2 of Run 020). Defined here
    so the bound name is importable from a single place.
    """


class UnsupportedCapabilityError(ValueError):
    """Reserved for execute_spec capability refusal (handoff 3 of Run 020).
    Defined here so the bound name is importable from a single place.
    """


class LargeInputRefusedError(ValueError):
    """Reserved for execute_spec large-input refusal (handoff 3 of Run 020).
    Defined here so the bound name is importable from a single place.
    """


# ── Per-harness manifests ───────────────────────────────────────────────
#
# These dicts are LITERAL — values are not computed at runtime. They are the
# source of truth for the manifest. Adding fields or groups beyond those
# listed in this module's docstring is a contract violation.
#
# Sources of truth per harness are stated inline above each dict.

# codex — resident TUI (adapter._codex_argv); one-shot form rejected by
# adapter.build_task_argv; MCP via adapter.build_codex_mcp_setup_argv.
_MANIFEST_CODEX = {
    "execution": {
        "terminal": True,
        "headless": False,
        "interactive": True,
    },
    "workspace": {
        "read_only": True,
        "workspace_write": True,
        "full_access": False,
    },
    "sessions": {
        "persistent_session": True,
        "session_resume": True,
        "mode": "fresh",
    },
    "extensions": {
        "skills": True,
        "mcp": True,
        "custom_tools": False,
        "repo_task_agent": False,
        "git_aware": False,
        "patch_output": False,
    },
    "automation": {
        "non_interactive": False,
        "deterministic_exit": False,
        "interrupt_safe": False,
    },
    "concurrency": {
        "parallel_readers": True,
        "exclusive_workspace_writer": False,
    },
    "lifecycle": {
        "interrupt_current_task": False,
        "resumable_session": False,
        "child_process_cleanup": False,
    },
    "models": {
        "openai_compatible_endpoint": False,
    },
}

# claude-code — resident interactive TUI coding client (DPMtF-launched per
# terminal.HARNESS_LABELS). Identical shape to codex except sessions.mode.
_MANIFEST_CLAUDE_CODE = {
    "execution": {
        "terminal": True,
        "headless": False,
        "interactive": True,
    },
    "workspace": {
        "read_only": True,
        "workspace_write": True,
        "full_access": False,
    },
    "sessions": {
        "persistent_session": True,
        "session_resume": True,
        "mode": "resident",
    },
    "extensions": {
        "skills": True,
        "mcp": True,
        "custom_tools": False,
        "repo_task_agent": False,
        "git_aware": False,
        "patch_output": False,
    },
    "automation": {
        "non_interactive": False,
        "deterministic_exit": False,
        "interrupt_safe": False,
    },
    "concurrency": {
        "parallel_readers": True,
        "exclusive_workspace_writer": False,
    },
    "lifecycle": {
        "interrupt_current_task": False,
        "resumable_session": False,
        "child_process_cleanup": False,
    },
    "models": {
        "openai_compatible_endpoint": False,
    },
}

# opencode — resident interactive TUI coding client (DPMtF-launched per
# terminal.HARNESS_LABELS). Identical shape to codex except sessions.mode.
_MANIFEST_OPENCODE = {
    "execution": {
        "terminal": True,
        "headless": False,
        "interactive": True,
    },
    "workspace": {
        "read_only": True,
        "workspace_write": True,
        "full_access": False,
    },
    "sessions": {
        "persistent_session": True,
        "session_resume": True,
        "mode": "resident",
    },
    "extensions": {
        "skills": True,
        "mcp": True,
        "custom_tools": False,
        "repo_task_agent": False,
        "git_aware": False,
        "patch_output": False,
    },
    "automation": {
        "non_interactive": False,
        "deterministic_exit": False,
        "interrupt_safe": False,
    },
    "concurrency": {
        "parallel_readers": True,
        "exclusive_workspace_writer": False,
    },
    "lifecycle": {
        "interrupt_current_task": False,
        "resumable_session": False,
        "child_process_cleanup": False,
    },
    "models": {
        "openai_compatible_endpoint": False,
    },
}

# dsh — headless one-shot (adapter.build_dsh_argv); MCP via
# adapter.build_dsh_mcp_patch_yml; measured cancel path
# (SIGINT → SIGTERM → SIGKILL) in invoke.run_argv.
_MANIFEST_DSH = {
    "execution": {
        "terminal": False,
        "headless": True,
        "interactive": False,
    },
    "workspace": {
        "read_only": False,
        "workspace_write": True,
        "full_access": False,
    },
    "sessions": {
        "persistent_session": False,
        "session_resume": False,
        "mode": "oneshot",
    },
    "extensions": {
        "skills": False,
        "mcp": True,
        "custom_tools": False,
        "repo_task_agent": False,
        "git_aware": False,
        "patch_output": False,
    },
    "automation": {
        "non_interactive": True,
        "deterministic_exit": True,
        "interrupt_safe": True,
    },
    "concurrency": {
        "parallel_readers": True,
        "exclusive_workspace_writer": False,
    },
    "lifecycle": {
        "interrupt_current_task": False,
        "resumable_session": False,
        "child_process_cleanup": False,
    },
    "models": {
        "openai_compatible_endpoint": False,
    },
}

# qwen — headless one-shot invoked by adapter.build_qwen_argv
# ([qwen, --approval-mode, <mode>, --include-directories <dir>*, -p, <task>]);
# model/endpoint live in the child env (adapter.build_qwen_env), not argv;
# measured cancel path (SIGINT → SIGTERM → SIGKILL) in invoke.run_argv.
# Grounded in the installed qwen CLI 0.22.0 (`qwen --help`) and the headless
# one-shot shape of the dsh manifest: qwen -p is a headless single-shot
# invocation (Qwen Code's own help: "use -p/--prompt for non-interactive
# mode"); sessions resume (-c/--continue, -r/--resume) is supported in the
# CLI but NOT in -p mode (one-shot here); qwen has both `qwen mcp`
# (extensions.mcp) and /skills in interactive mode (extensions.skills);
# automation.deterministic_exit is the bound D1 fact for -p; automation.
# interrupt_safe mirrors dsh — invoke.run_argv's measured cancel path.
_MANIFEST_QWEN = {
    "execution": {
        "terminal": False,
        "headless": True,
        "interactive": False,
    },
    "workspace": {
        "read_only": False,
        "workspace_write": True,
        "full_access": False,
    },
    "sessions": {
        "persistent_session": False,
        "session_resume": True,
        "mode": "oneshot",
    },
    "extensions": {
        "skills": True,
        "mcp": True,
        "custom_tools": False,
        "repo_task_agent": False,
        "git_aware": False,
        "patch_output": False,
    },
    "automation": {
        "non_interactive": True,
        "deterministic_exit": True,
        "interrupt_safe": True,
    },
    "concurrency": {
        "parallel_readers": True,
        "exclusive_workspace_writer": False,
    },
    "lifecycle": {
        "interrupt_current_task": False,
        "resumable_session": False,
        "child_process_cleanup": False,
    },
    "models": {
        "openai_compatible_endpoint": False,
    },
}


# goose — headless one-shot invoked by adapter.build_goose_argv
# ([goose, run, --no-session, -q, --max-turns, 1, -t, <task>]); model /
# provider / endpoint / api key live in the child env
# (adapter.build_goose_env), not argv; measured cancel path
# (SIGINT → SIGTERM → SIGKILL) lives in invoke.run_argv (inherited from
# the dsh / qwen path).
#
# Ground-truth (bound against the installed goose 1.47.0 build, see
# ``goose run --help``, ``goose --help``, ``goose info``):
#   - execution:  ``goose run`` is the headless one-shot subcommand (vs
#                 ``session``/``tui`` which are interactive). Hence
#                 headless=True, terminal=False, interactive=False.
#   - workspace:  Goose has no explicit access tier in ``goose run
#                 --help`` — the default behaviour is workspace-write
#                 (the tool can read/write under the working directory).
#                 full_access is left False (matches qwen).
#   - sessions:   headless ``run`` does not create a session file when
#                 ``--no-session`` is set (and our adapter always sets
#                 it). Hence persistent_session=False,
#                 session_resume=False, mode="oneshot".
#   - extensions: goose IS the first EXTENSIBLE harness — measured
#                 honestly from the build:
#                   * mcp           True   (``goose mcp`` subcommand,
#                                       ``--with-streamable-http-
#                                       extension <url>``,
#                                       ``--with-extension`` for stdio)
#                   * skills        True   (``goose skills`` subcommand)
#                   * custom_tools  True   (``goose plugin install <git>``,
#                                       ``--with-extension <cmd>`` for
#                                       ad-hoc stdio tools, and the
#                                       bundled extensions:
#                                       ``developer``, ``tutorial``,
#                                       ``computercontroller``,
#                                       ``memory``, ``scheduler``,
#                                       ``code_execution`` — verified by
#                                       ``goose run --with-builtin <name>``
#                                       accepting each)
#                 The qwen extensions values are NOT copied — Goose
#                 supports all three (qwen lacks custom_tools).
#   - automation: ``--no-session -q --max-turns 1`` is the bound
#                 non-interactive surface; there is no ``--dangerously-
#                 skip-permissions`` flag (verified — ``goose run --help``
#                 does not list one). Hence non_interactive=True,
#                 deterministic_exit=True, interrupt_safe=True (the
#                 invoke layer's measured SIGINT → SIGTERM → SIGKILL
#                 path applies unchanged).
_MANIFEST_GOOSE = {
    "execution": {
        "terminal": False,
        "headless": True,
        "interactive": False,
    },
    "workspace": {
        "read_only": False,
        "workspace_write": True,
        "full_access": False,
    },
    "sessions": {
        "persistent_session": False,
        "session_resume": False,
        "mode": "oneshot",
    },
    "extensions": {
        "skills": True,
        "mcp": True,
        "custom_tools": True,
        "repo_task_agent": False,
        "git_aware": False,
        "patch_output": False,
    },
    "automation": {
        "non_interactive": True,
        "deterministic_exit": True,
        "interrupt_safe": True,
    },
    "concurrency": {
        "parallel_readers": True,
        "exclusive_workspace_writer": False,
    },
    "lifecycle": {
        "interrupt_current_task": False,
        "resumable_session": False,
        "child_process_cleanup": False,
    },
    "models": {
        "openai_compatible_endpoint": False,
    },
}


# aider — headless one-shot invoked by adapter.build_aider_argv
# ([<aider_bin>, --yes-always, --no-auto-commits, --no-dirty-commits,
# --model <model>?, --message <task>?]); model is a CLI flag (mirror
# sweagent, NOT qwen/goose); API key is inherited from the parent
# environment (aider reads OPENAI_API_KEY itself); build_aider_env
# returns {} (no load-bearing env). The non-interactive +
# no-auto-commit trio is ALWAYS emitted in argv — aider commits by
# default (the git-policy stays with the Human per Run 027 / HA-4 §2).
#
# Ground-truth (bound against the installed aider 0.86.2 build, see
# ``aider --help``, ``aider --version``):
#   - execution:  ``aider --message <task> --yes-always`` is the
#                 headless one-shot form. headless=True, terminal=False,
#                 interactive=False.
#   - workspace:  aider edits the user's repo in place; no access-tier
#                 flag surfaces. read_only=False, workspace_write=True,
#                 full_access=False (mirror qwen/goose/sweagent).
#   - sessions:   ``aider --message --yes-always`` creates no persistent
#                 session — persistent_session=False, session_resume=False,
#                 mode="oneshot".
#   - extensions: aider does NOT expose a goose-style skills/mcp/plugin
#                 surface; the three universal-extensions keys are False
#                 (measured honestly). The TWO NEW non-universal keys
#                 ``git_aware`` and ``patch_output`` are True (per
#                 ROADMAP §3 — aider is a specialized git/patch harness).
#   - automation: ``--message --yes-always`` is intrinsically
#                 non-interactive; non_interactive=True,
#                 deterministic_exit=True, interrupt_safe=True.
_MANIFEST_AIDER = {
    "execution": {
        "terminal": False,
        "headless": True,
        "interactive": False,
    },
    "workspace": {
        "read_only": False,
        "workspace_write": True,
        "full_access": False,
    },
    "sessions": {
        "persistent_session": False,
        "session_resume": False,
        "mode": "oneshot",
    },
    "extensions": {
        "skills": False,
        "mcp": False,
        "custom_tools": False,
        "git_aware": True,
        "patch_output": True,
        "repo_task_agent": False,
    },
    "automation": {
        "non_interactive": True,
        "deterministic_exit": True,
        "interrupt_safe": True,
    },
    "concurrency": {
        "parallel_readers": True,
        "exclusive_workspace_writer": False,
    },
    "lifecycle": {
        "interrupt_current_task": False,
        "resumable_session": False,
        "child_process_cleanup": False,
    },
    "models": {
        "openai_compatible_endpoint": False,
    },
}

# ── Public API ─────────────────────────────────────────────────────────

# sweagent — headless one-shot invoked by adapter.build_sweagent_argv
# ([<sweagent_bin>, run, --agent.model.name <model>, --env.repo.path <path>?,
# --problem_statement.text <task>?]); the SWE-agent env binding
# (SWE_AGENT_CONFIG_DIR / TOOLS_DIR / TRAJECTORY_DIR) lives in
# adapter.build_sweagent_env, and is ALWAYS set (the bare CLI asserts on
# CONFIG_DIR.is_dir() — verified). SWE-agent is a SPECIALIZED repo-task
# agent — measured against the installed sweagent 1.1.0 build
# (git checkout at $(home)/tools/SWE-agent/), see ``sweagent --help``,
# ``sweagent run --help``, ``sweagent run --help_option
# sweagent.agent.problem_statement.TextProblemStatement``.
#
# Ground-truth (bound against the installed build):
#   - execution:  ``sweagent run`` is the headless one-shot subcommand.
#                 The agent operates on a repo in a deployment (Docker by
#                 default; ``--env.deployment.type local`` runs against a
#                 local repo path). Hence headless=True, terminal=False,
#                 interactive=False (matches the qwen/goose headless-
#                 oneshot envelope).
#   - workspace:  SWE-agent has no granular workspace flag in ``sweagent
#                 run --help`` — its access tier is bound by the underlying
#                 deployment container / local-repo mount. For the
#                 local-repo form the agent reads and writes under the
#                 repo root (workspace_write=True); no full_access flag is
#                 surfaced (full_access=False, mirroring qwen/goose).
#   - sessions:   ``sweagent run`` is single-instance — there is no
#                 session-resume surface in ``sweagent run --help``. Hence
#                 persistent_session=False, session_resume=False,
#                 mode="oneshot" (the qwen/goose envelope).
#   - extensions: SWE-agent does NOT expose a goose-style skills/mcp/plugin
#                 surface (``sweagent --help`` lists no subcommands for
#                 skills/mcp/plugins — its extensibility lives in the tool
#                 bundles inside ``tools/``, exposed via the ``bundles:``
#                 key in the agent config, NOT via CLI flags). Hence
#                 skills=False, mcp=False, custom_tools=False (measured
#                 honestly — NOT copied from qwen). The NEW non-universal
#                 key ``repo_task_agent`` is True: SWE-agent IS the first
#                 specialized repo-task agent in the set (ROADMAP §3's
#                 capability-rule — added per-harness, not as a global
#                 extension).
#   - automation: ``sweagent run`` is a single-shot invocation bound to
#                 ``--max-steps`` / a per-step budget (verify — the
#                 sweagent run --help docstring names ``RunSingleActionConfig``
#                 in the ``actions`` sub-key). The agent returns on its
#                 own with no stdin wait. Hence non_interactive=True,
#                 deterministic_exit=True, interrupt_safe=True (the invoke
#                 layer's measured SIGINT → SIGTERM → SIGKILL path applies
#                 unchanged).
_MANIFEST_SWEAGENT = {
    "execution": {
        "terminal": False,
        "headless": True,
        "interactive": False,
    },
    "workspace": {
        "read_only": False,
        "workspace_write": True,
        "full_access": False,
    },
    "sessions": {
        "persistent_session": False,
        "session_resume": False,
        "mode": "oneshot",
    },
    "extensions": {
        "skills": False,
        "mcp": False,
        "custom_tools": False,
        "repo_task_agent": True,
        "git_aware": False,
        "patch_output": False,
    },
    "automation": {
        "non_interactive": True,
        "deterministic_exit": True,
        "interrupt_safe": True,
    },
    "concurrency": {
        "parallel_readers": True,
        "exclusive_workspace_writer": False,
    },
    "lifecycle": {
        "interrupt_current_task": False,
        "resumable_session": False,
        "child_process_cleanup": False,
    },
    "models": {
        "openai_compatible_endpoint": False,
    },
}



# crush — headless one-shot invoked by adapter.build_crush_argv
# ([<crush_bin>, run, --yolo, --quiet, --model, <model>?, <task>?]);
# --yolo is the non-interactive auto-accept binding (analogous to qwen's
# --approval-mode yolo and aider's --yes-always); --quiet hides the spinner
# (mirror goose's -q, so stdout is parseable); --model is a CLI flag (mirror
# aider / sweagent, NOT qwen / goose). API key travels in the child env
# (adapter.build_crush_env wires OPENAI_API_KEY from a NAMED env var; crush
# reads OPENAI_API_KEY natively — README §"API Keys"); base URL is configured
# via crushrc (README §"Custom Providers" — `provider add --base-url`), NOT
# via a standard env var — the OPENAI_BASE_URL wiring exists for qwen/goose
# pattern-parity only (honest boundary documented in adapter.build_crush_env
# and config.get_crush_base_url).
#
# Ground-truth (bound against the installed crush v0.91.0 build, see
# ``crush --help``, ``crush run --help``, ``crush --version``, and the npm
# package README at
# the npm package README bundled with the @charmland/crush install):
#   - execution:  ``crush run`` is the headless one-shot subcommand ("Run a
#                 single non-interactive prompt" per ``crush run --help``).
#                 Hence headless=True, terminal=False, interactive=False
#                 (mirror qwen/goose/sweagent/aider).
#   - workspace:  crush edits the user's project in cwd (the README's
#                 "Extensible" bullet lists MCPs and Skills, both of which
#                 operate on the working directory). No explicit access-tier
#                 flag surfaces (verified ``crush --help``). read_only=False,
#                 workspace_write=True, full_access=False (mirror qwen/goose/
#                 sweagent/aider).
#   - sessions:   crush IS session-based (README feature bullet "Session-Based:
#                 maintain multiple work sessions and contexts per project";
#                 --session {id}, --continue, and ``crush session`` manage
#                 sessions). persistent_session=True, session_resume=True.
#                 mode="oneshot" because the ADAPTER launches the one-shot
#                 ``crush run`` form (the qwen precedent: session_resume=True
#                 with mode="oneshot").
#   - extensions: crush supports skills=True (Agent Skills open standard per
#                 README §"Agent Skills"), mcp=True (MCP via ``mcp add``,
#                 three transports http/stdio/sse per README §"MCPs").
#                 custom_tools=False: crush's custom-tool surface IS MCP
#                 (no separate goose-style plugin install). The three
#                 specialized keys (repo_task_agent, git_aware,
#                 patch_output) are False (chat-style — GOAL.md §3b: PRESENT
#                 and False, never omitted — the universality tests derive
#                 the roster from SUPPORTED_HARNESSES + EXPERIMENTAL_HARNESSES
#                 and assert every harness declares all three).
#   - automation: ``crush run --yolo`` is the bound non-interactive surface;
#                 deterministic_exit=True (single-shot returns on its own
#                 with no stdin wait); interrupt_safe=True (the invoke
#                 layer's measured SIGINT → SIGTERM → SIGKILL path applies
#                 unchanged).
_MANIFEST_CRUSH = {
    "execution": {
        "terminal": False,
        "headless": True,
        "interactive": False,
    },
    "workspace": {
        "read_only": False,
        "workspace_write": True,
        "full_access": False,
    },
    "sessions": {
        "persistent_session": True,
        "session_resume": True,
        "mode": "oneshot",
    },
    "extensions": {
        "skills": True,
        "mcp": True,
        "custom_tools": False,
        "repo_task_agent": False,
        "git_aware": False,
        "patch_output": False,
    },
    "automation": {
        "non_interactive": True,
        "deterministic_exit": True,
        "interrupt_safe": True,
    },
    "concurrency": {
        "parallel_readers": True,
        "exclusive_workspace_writer": False,
    },
    "lifecycle": {
        "interrupt_current_task": False,
        "resumable_session": False,
        "child_process_cleanup": False,
    },
    "models": {
        "openai_compatible_endpoint": False,
    },
}


# whip — openai-compatible LLM adapter invoked via whip v0.4.0 binary.
# Uses -cautious mode (interactive, not read-only enforcement), adapts
# to the user's current workspace scope, and is launched headless via
# stdin prompt injection through the whip adapter interface. Whip selects
# models from its own config (no runtime model swapping), communicates
# via the openai /api/chat/completions endpoint, resolves its model from
# whip config (never hardcoded), and stores API keys via env indirection
# (apiKeyEnv in whip config, never on disk). Capabilities marked UNKNOWN
# require verification against the pinned v0.4.0 binary by D4 tests;
# UNSUPPORTED where the binary architecture precludes the capability.
_MANIFEST_WHIP = {
    "execution": {
        "terminal": True,
        "headless": True,
        "interactive": False,
    },
    "workspace": {
        "read_only": False,
        "workspace_write": True,
        "full_access": False,
    },
    "sessions": {
        "persistent_session": False,
        "session_resume": False,
        "mode": "oneshot",
    },
    "extensions": {
        "skills": False,
        "mcp": False,
        "custom_tools": False,
        "repo_task_agent": False,
        "git_aware": False,
        "patch_output": False,
    },
    "automation": {
        "non_interactive": True,
        "deterministic_exit": True,
        "interrupt_safe": True,
    },
    "concurrency": {
        "parallel_readers": True,
        "exclusive_workspace_writer": False,
    },
    "lifecycle": {
        "interrupt_current_task": True,
        "resumable_session": False,
        "child_process_cleanup": False,
    },
    "models": {
        "openai_compatible_endpoint": True,
    },
}


_MANIFEST_SIMPLE_HARNESS = {
    "execution": {
        "terminal": True,
        "headless": True,
        "interactive": True,
    },
    "workspace": {
        "read_only": True,
        "workspace_write": True,
        "full_access": True,
    },
    "sessions": {
        "persistent_session": False,
        "session_resume": False,
        "mode": "fresh",
    },
    "extensions": {
        "skills": True,
        "mcp": True,
        "custom_tools": False,
        "repo_task_agent": False,
        "git_aware": False,
        "patch_output": False,
    },
    "automation": {
        "non_interactive": True,
        "deterministic_exit": True,
        "interrupt_safe": True,
    },
    "concurrency": {
        "parallel_readers": True,
        "exclusive_workspace_writer": True,
    },
    "lifecycle": {
        "interrupt_current_task": True,
        "resumable_session": False,
        "child_process_cleanup": True,
    },
    "models": {
        "openai_compatible_endpoint": True,
    },
}


#: The set of harness keys that ``get_capabilities`` accepts.
SUPPORTED_HARNESSES = ("codex", "claude-code", "opencode", "dsh", "qwen", "goose", "crush", "whip", "simple-harness")

#: The experimental set — registered in the adapter surface but NOT exposed
#: as defaults. A harness in this tuple is gated by
#: ``config.get_experimental_enabled_harnesses()``; an empty enable-set
#: refuses both build_*_argv calls with a typed ValueError BEFORE any
#: subprocess. (Run 027 / HA-4 §1 D3 — the D3 experimental gate.)
EXPERIMENTAL_HARNESSES = ("sweagent", "aider")

#: Normalized capability value vocabulary used throughout the capability
#: model. Every capability value assigned to a manifest section must come
#: from this tuple.
CAPABILITY_VALUES = ("SUPPORTED", "EMULATED", "UNSUPPORTED", "UNKNOWN")


def get_capabilities(harness) -> dict:
    """Return the normalized capability manifest for ``harness``.

    The returned dict has EXACTLY eight groups (``execution``, ``workspace``,
    ``sessions``, ``extensions``, ``automation``, ``concurrency``,
    ``lifecycle``, ``models``) with the fields documented in this module's
    docstring; no field or group is added beyond those.

    Raises :class:`UnknownHarnessError` (a :class:`ValueError` subclass) when
    ``harness`` is not one of ``codex``, ``claude-code``, ``opencode``,
    ``dsh``, ``qwen``, ``goose``, ``crush``, ``whip``, ``simple-harness``,
    ``sweagent``, ``aider``. The
    error message names the unknown harness.
    """
    if harness == "codex":
        # Return a fresh copy so callers cannot mutate the module-level dict.
        return {
            "execution": dict(_MANIFEST_CODEX["execution"]),
            "workspace": dict(_MANIFEST_CODEX["workspace"]),
            "sessions": dict(_MANIFEST_CODEX["sessions"]),
            "extensions": dict(_MANIFEST_CODEX["extensions"]),
            "automation": dict(_MANIFEST_CODEX["automation"]),
            "concurrency": dict(_MANIFEST_CODEX["concurrency"]),
            "lifecycle": dict(_MANIFEST_CODEX["lifecycle"]),
            "models": dict(_MANIFEST_CODEX["models"]),
        }
    if harness == "claude-code":
        return {
            "execution": dict(_MANIFEST_CLAUDE_CODE["execution"]),
            "workspace": dict(_MANIFEST_CLAUDE_CODE["workspace"]),
            "sessions": dict(_MANIFEST_CLAUDE_CODE["sessions"]),
            "extensions": dict(_MANIFEST_CLAUDE_CODE["extensions"]),
            "automation": dict(_MANIFEST_CLAUDE_CODE["automation"]),
            "concurrency": dict(_MANIFEST_CLAUDE_CODE["concurrency"]),
            "lifecycle": dict(_MANIFEST_CLAUDE_CODE["lifecycle"]),
            "models": dict(_MANIFEST_CLAUDE_CODE["models"]),
        }
    if harness == "opencode":
        return {
            "execution": dict(_MANIFEST_OPENCODE["execution"]),
            "workspace": dict(_MANIFEST_OPENCODE["workspace"]),
            "sessions": dict(_MANIFEST_OPENCODE["sessions"]),
            "extensions": dict(_MANIFEST_OPENCODE["extensions"]),
            "automation": dict(_MANIFEST_OPENCODE["automation"]),
            "concurrency": dict(_MANIFEST_OPENCODE["concurrency"]),
            "lifecycle": dict(_MANIFEST_OPENCODE["lifecycle"]),
            "models": dict(_MANIFEST_OPENCODE["models"]),
        }
    if harness == "dsh":
        return {
            "execution": dict(_MANIFEST_DSH["execution"]),
            "workspace": dict(_MANIFEST_DSH["workspace"]),
            "sessions": dict(_MANIFEST_DSH["sessions"]),
            "extensions": dict(_MANIFEST_DSH["extensions"]),
            "automation": dict(_MANIFEST_DSH["automation"]),
            "concurrency": dict(_MANIFEST_DSH["concurrency"]),
            "lifecycle": dict(_MANIFEST_DSH["lifecycle"]),
            "models": dict(_MANIFEST_DSH["models"]),
        }
    if harness == "qwen":
        return {
            "execution": dict(_MANIFEST_QWEN["execution"]),
            "workspace": dict(_MANIFEST_QWEN["workspace"]),
            "sessions": dict(_MANIFEST_QWEN["sessions"]),
            "extensions": dict(_MANIFEST_QWEN["extensions"]),
            "automation": dict(_MANIFEST_QWEN["automation"]),
            "concurrency": dict(_MANIFEST_QWEN["concurrency"]),
            "lifecycle": dict(_MANIFEST_QWEN["lifecycle"]),
            "models": dict(_MANIFEST_QWEN["models"]),
        }
    if harness == "goose":
        return {
            "execution": dict(_MANIFEST_GOOSE["execution"]),
            "workspace": dict(_MANIFEST_GOOSE["workspace"]),
            "sessions": dict(_MANIFEST_GOOSE["sessions"]),
            "extensions": dict(_MANIFEST_GOOSE["extensions"]),
            "automation": dict(_MANIFEST_GOOSE["automation"]),
            "concurrency": dict(_MANIFEST_GOOSE["concurrency"]),
            "lifecycle": dict(_MANIFEST_GOOSE["lifecycle"]),
            "models": dict(_MANIFEST_GOOSE["models"]),
        }
    if harness == "sweagent":
        return {
            "execution": dict(_MANIFEST_SWEAGENT["execution"]),
            "workspace": dict(_MANIFEST_SWEAGENT["workspace"]),
            "sessions": dict(_MANIFEST_SWEAGENT["sessions"]),
            "extensions": dict(_MANIFEST_SWEAGENT["extensions"]),
            "automation": dict(_MANIFEST_SWEAGENT["automation"]),
            "concurrency": dict(_MANIFEST_SWEAGENT["concurrency"]),
            "lifecycle": dict(_MANIFEST_SWEAGENT["lifecycle"]),
            "models": dict(_MANIFEST_SWEAGENT["models"]),
        }
    if harness == "aider":
        return {
            "execution": dict(_MANIFEST_AIDER["execution"]),
            "workspace": dict(_MANIFEST_AIDER["workspace"]),
            "sessions": dict(_MANIFEST_AIDER["sessions"]),
            "extensions": dict(_MANIFEST_AIDER["extensions"]),
            "automation": dict(_MANIFEST_AIDER["automation"]),
            "concurrency": dict(_MANIFEST_AIDER["concurrency"]),
            "lifecycle": dict(_MANIFEST_AIDER["lifecycle"]),
            "models": dict(_MANIFEST_AIDER["models"]),
        }
    if harness == "crush":
        return {
            "execution": dict(_MANIFEST_CRUSH["execution"]),
            "workspace": dict(_MANIFEST_CRUSH["workspace"]),
            "sessions": dict(_MANIFEST_CRUSH["sessions"]),
            "extensions": dict(_MANIFEST_CRUSH["extensions"]),
            "automation": dict(_MANIFEST_CRUSH["automation"]),
            "concurrency": dict(_MANIFEST_CRUSH["concurrency"]),
            "lifecycle": dict(_MANIFEST_CRUSH["lifecycle"]),
            "models": dict(_MANIFEST_CRUSH["models"]),
        }
    if harness == "whip":
        return {
            "execution": dict(_MANIFEST_WHIP["execution"]),
            "workspace": dict(_MANIFEST_WHIP["workspace"]),
            "sessions": dict(_MANIFEST_WHIP["sessions"]),
            "extensions": dict(_MANIFEST_WHIP["extensions"]),
            "automation": dict(_MANIFEST_WHIP["automation"]),
            "concurrency": dict(_MANIFEST_WHIP["concurrency"]),
            "lifecycle": dict(_MANIFEST_WHIP["lifecycle"]),
            "models": dict(_MANIFEST_WHIP["models"]),
        }
    if harness == "simple-harness":
        return {
            "execution": dict(_MANIFEST_SIMPLE_HARNESS["execution"]),
            "workspace": dict(_MANIFEST_SIMPLE_HARNESS["workspace"]),
            "sessions": dict(_MANIFEST_SIMPLE_HARNESS["sessions"]),
            "extensions": dict(_MANIFEST_SIMPLE_HARNESS["extensions"]),
            "automation": dict(_MANIFEST_SIMPLE_HARNESS["automation"]),
            "concurrency": dict(_MANIFEST_SIMPLE_HARNESS["concurrency"]),
            "lifecycle": dict(_MANIFEST_SIMPLE_HARNESS["lifecycle"]),
            "models": dict(_MANIFEST_SIMPLE_HARNESS["models"]),
        }
    raise UnknownHarnessError(f"unknown harness: {harness!r}")
