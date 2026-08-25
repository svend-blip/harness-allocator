"""HarnessAdapter — builds the shell command or argv that launches a harness.

All string/list builders, no process spawning, so the surface is unit-testable
without touching an API. The allocator owns command syntax here; callers ask
for behaviour (``execute``), not a command string.

Model boundary: ``model_target`` is the ALREADY-RESOLVED model target supplied
by Model Allocator (upstream of this package). This module only renders it into
a harness's native CLI syntax — it never resolves, selects, defaults, or
substitutes a model.

Two equivalent surfaces are exposed for each native harness:

- a **shell-string** form (``build_*_invocation``) for callers that need to
  log, paste, or otherwise display the command;
- an **argv-list** form (``build_*_argv``) for safe, exact subprocess
  execution. The argv form is what :func:`~harness_allocator.invoke.execute`
  uses, so a 20k+ character prompt with hundreds of embedded newlines is
  delivered as exactly one argv element and produces exactly one harness
  invocation — no shell interpolation, no shlex round-trip.
"""

from __future__ import annotations

from pathlib import Path
import shlex

from . import config
from .capabilities import EXPERIMENTAL_HARNESSES
from .definition import model_target_identity


def build_launch_command(harness, model_target=None, task=None, cfg=None) -> str:
    """The shell command that starts a native harness.

    - ``codex`` -> the resident TUI command (``codex -m <model_target>``).
    - ``dsh``   -> the one-shot headless invocation (``dsh --profile <profile>
      [--patch <patch>] [task]``).

    ``model_target`` is the already-resolved target, rendered verbatim (codex)
    or not used for selection at all (dsh, whose model is pinned by the
    profile/patch the caller configured). Raises ``ValueError`` for a
    non-native harness. ``cfg`` defaults to this package's own config and is
    injectable for tests.
    """
    if cfg is None:
        cfg = config
    if harness == "codex":
        return _codex_command(model_target, cfg)
    if harness == "dsh":
        return build_dsh_invocation(model_target, task, cfg)
    if harness == "qwen":
        return build_qwen_invocation(model_target, task, cfg)
    if harness == "goose":
        return build_goose_invocation(model_target, task, cfg)
    if harness == "sweagent":
        return build_sweagent_invocation(model_target, task, cfg)
    if harness == "aider":
        return build_aider_invocation(model_target, task, cfg)
    raise ValueError(f"not a native harness: {harness!r}")


def build_launch_argv(harness, model_target=None, task=None, cfg=None) -> list:
    """The argv list that starts a native harness (no shell interpolation)."""
    if cfg is None:
        cfg = config
    if harness == "codex":
        return _codex_argv(model_target, cfg)
    if harness == "dsh":
        return build_dsh_argv(model_target, task, cfg)
    if harness == "qwen":
        return build_qwen_argv(model_target, task, cfg)
    if harness == "goose":
        return build_goose_argv(model_target, task, cfg)
    if harness == "sweagent":
        return build_sweagent_argv(model_target, task, cfg)
    if harness == "aider":
        return build_aider_argv(model_target, task, cfg)
    raise ValueError(f"not a native harness: {harness!r}")


def build_dsh_invocation(model_target=None, task=None, cfg=None) -> str:
    """The one-shot DeepSeek Harness invocation as a shell command string.

    ``dsh --profile <profile> [--patch <patch>] [task]``. ``task`` is quoted
    and appended as the final argument; profile and patch come from ``cfg``
    (``headless`` / empty by default). ``model_target`` is accepted for
    call-shape compatibility and deliberately NOT used: the DeepSeek Harness
    model is pinned by the profile/patch the caller (Model Allocator) resolved,
    so the allocator must not re-select it here.
    """
    return _join_argv(build_dsh_argv(model_target, task, cfg))


def build_dsh_argv(model_target=None, task=None, cfg=None) -> list:
    """The one-shot DeepSeek Harness invocation as an argv list.

    ``[dsh, --profile, <profile>, --patch, <patch>?, <task>?]``. This is the
    safe surface for subprocess execution: the complete ``task`` (any size,
    any embedded newlines) is passed as one argv element with no shell
    interpolation. ``model_target`` is accepted for call-shape compatibility
    and deliberately NOT used: the DeepSeek Harness model is pinned by the
    profile/patch the caller (Model Allocator) resolved, so the allocator
    must not re-select it here.
    """
    if cfg is None:
        cfg = config
    parts = shlex.split(cfg.get_dsh_bin())
    parts += ["--profile", cfg.get_dsh_profile()]
    patch = (cfg.get_dsh_patch_path() or "").strip()
    if patch:
        parts += ["--patch", patch]
    if task:
        parts += [task]
    return parts


def build_task_invocation(harness, model_target=None, task=None, cfg=None) -> str:
    """Build the shell command that executes ``task`` through ``harness``.

    The harness-neutral entry point the terminal uses: one-shot harnesses
    return the single command that runs ``task``; resident TUIs (codex,
    claude-code, opencode) have no one-shot form and raise.
    """
    if harness == "dsh":
        return build_dsh_invocation(model_target, task, cfg)
    if harness == "qwen":
        return build_qwen_invocation(model_target, task, cfg)
    if harness == "goose":
        return build_goose_invocation(model_target, task, cfg)
    if harness == "sweagent":
        return build_sweagent_invocation(model_target, task, cfg)
    if harness == "aider":
        return build_aider_invocation(model_target, task, cfg)
    raise ValueError(f"no one-shot task invocation for harness {harness!r}")


def build_task_argv(harness, model_target=None, task=None, cfg=None) -> list:
    """Build the argv list that executes ``task`` through ``harness``.

    The argv form is what :func:`~harness_allocator.invoke.execute` uses to
    spawn the harness. The full ``task`` (any size, any embedded newlines) is
    one argv element, so subprocess.Popen creates one child process — exactly
    one harness invocation, regardless of how many newlines ``task`` contains.
    """
    if harness == "dsh":
        return build_dsh_argv(model_target, task, cfg)
    if harness == "qwen":
        return build_qwen_argv(model_target, task, cfg)
    if harness == "goose":
        return build_goose_argv(model_target, task, cfg)
    if harness == "sweagent":
        return build_sweagent_argv(model_target, task, cfg)
    if harness == "aider":
        return build_aider_argv(model_target, task, cfg)
    raise ValueError(f"no one-shot task invocation for harness {harness!r}")


def build_qwen_invocation(model_target=None, task=None, cfg=None) -> str:
    """The one-shot Qwen Code invocation as a shell command string.

    ``[<qwen_bin>, --approval-mode, <mode>, --include-directories <dir>*, -p, <task>]``.
    Model and endpoint NEVER appear in argv — they travel in the child env
    (see :func:`build_qwen_env`). ``task`` is appended as the final argument
    when supplied; ``model_target`` is accepted for call-shape compatibility
    and deliberately NOT used (the allocator never selects the model).
    """
    return _join_argv(build_qwen_argv(model_target, task, cfg))


def build_qwen_argv(model_target=None, task=None, cfg=None) -> list:
    """The one-shot Qwen Code invocation as an argv list.

    ``[<qwen_bin>, --approval-mode, <mode>, --include-directories <dir>*, -p, <task>]``.
    The complete ``task`` (any size, any embedded newlines) is one argv
    element — no shlex round-trip, no shell interpolation. The model and
    endpoint travel in the child env (:func:`build_qwen_env`), not in argv;
    ``model_target`` is accepted for call-shape compatibility and deliberately
    NOT used.

    ``--include-directories <dir>`` is REPEATABLE — one flag per configured
    dir, omitted entirely when none are configured. ``-p <task>`` is appended
    only when a task is supplied (the launch form omits it).
    """
    if cfg is None:
        cfg = config
    parts = shlex.split(cfg.get_qwen_bin())
    if not parts:
        # Missing-binary refusal (D4 — Run 022 / HA-2): the resolved
        # binary is empty/whitespace. Refuse with a typed ValueError
        # naming the harness — no filesystem existence check, the
        # adapter is a pure string/list builder (no I/O). The refusal
        # surfaces BEFORE any subprocess (the execute layer will
        # catch it via its `except Exception` clause).
        raise ValueError(
            "qwen binary is not configured "
            "(empty QWEN_BIN / [qwen] bin)"
        )
    parts += ["--approval-mode", cfg.get_qwen_approval_mode()]
    for d in cfg.get_qwen_add_dirs():
        d = (d or "").strip()
        if d:
            parts += ["--include-directories", d]
    if task:
        parts += ["-p", task]
    return parts


def build_qwen_env(model_target=None, cfg=None) -> dict:
    """The child-env override dict for a one-shot Qwen Code invocation.

    Endpoint wiring is env-based, not flag-based. The dict carries:

    - ``OPENAI_BASE_URL`` — the resolved base URL FORCED to end in ``/v1``
      (appended when non-empty and not already ending in it). OMITTED when the
      base URL is empty (Qwen Code's default endpoint).
    - ``OPENAI_MODEL`` — the resolved model target VERBATIM. OMITTED when
      empty.
    - ``OPENAI_API_KEY`` — the VALUE read from the environment variable NAMED
      by ``cfg.get_qwen_api_key_env()`` (the config value is a NAME, never a
      secret). OMITTED when the name is empty or the named variable is unset
      / empty.

    Returns an empty dict when there is nothing to set (no base URL, no model,
    no key) — the caller can then fall back to inheriting the parent env. This
    builder never reads or returns a secret value of its own; the key is read
    from the NAMED environment variable the config identifies.
    """
    if cfg is None:
        cfg = config
    env: dict = {}
    base_url = (cfg.get_qwen_base_url() or "").strip()
    if base_url:
        if not base_url.endswith("/v1"):
            base_url = base_url.rstrip("/") + "/v1"
        env["OPENAI_BASE_URL"] = base_url
    model = model_target_identity(model_target)
    if model:
        env["OPENAI_MODEL"] = model
    name = (cfg.get_qwen_api_key_env() or "").strip()
    if name:
        import os
        key = os.environ.get(name)
        if key:
            env["OPENAI_API_KEY"] = key
    return env



def build_goose_invocation(model_target=None, task=None, cfg=None) -> str:
    """The one-shot Goose invocation as a shell command string.

    ``[<goose_bin>, run, --no-session, -q, --max-turns, 1, -t, <task>]``.
    Model, provider, endpoint and api key NEVER appear in argv — they
    travel in the child env (see :func:`build_goose_env`). ``task`` is
    appended as the final argument when supplied; ``model_target`` is
    accepted for call-shape compatibility and deliberately NOT used (the
    allocator never selects the model — it travels in the env as
    ``GOOSE_MODEL``).
    """
    return _join_argv(build_goose_argv(model_target, task, cfg))


def build_goose_argv(model_target=None, task=None, cfg=None) -> list:
    """The one-shot Goose (Block AI agent CLI) invocation as an argv list.

    ``[<goose_bin>, run, --no-session, -q, --max-turns, 1, -t, <task>]``.

    Bound against the installed goose build 1.47.0 (see
    ``goose run --help``):

    - ``run`` is the headless one-shot subcommand (vs ``session``/``tui``).
    - ``--no-session`` — "Execute commands without creating or using a
      session file. Useful for automated runs." (per ``goose run --help``).
    - ``-q`` (alias ``--quiet``) — "Suppress non-response output, printing
      only the model response to stdout." Bounds the spinner / progress
      chatter so stdout is parseable.
    - ``--max-turns 1`` — caps the agent at a single turn (no stdin waits,
      no permission prompts — the headless one-shot form is intrinsically
      non-interactive; there is no ``--dangerously-skip-permissions`` flag
      in ``goose run --help``).
    - ``-t <task>`` — input text containing the task (use this in lieu of
      the ``--instructions`` flag for one-shot text).

    The complete ``task`` (any size, any embedded newlines) is one argv
    element — no shlex round-trip, no shell interpolation. The model
    identity, provider name, endpoint URL, and API key all travel in the
    child env (:func:`build_goose_env`), not in argv; ``model_target`` is
    accepted for call-shape compatibility and deliberately NOT used.

    bin from ``cfg.get_goose_bin()``; missing/empty bin raises a typed
    ``ValueError`` naming ``goose`` (pure string builder, NO filesystem
    existence check) — the refusal surfaces BEFORE any subprocess. There
    is NO CLI flag for ``--include-directories`` on the goose build, so
    the configured add_dirs are not emitted in argv (the ``[goose] add_dirs``
    config key is reserved for callers / future recipe-mode wiring).
    ``task`` appended only when supplied; the launch form omits ``-t``.
    """
    if cfg is None:
        cfg = config
    parts = shlex.split(cfg.get_goose_bin())
    if not parts:
        # Missing-binary refusal (TG7 / D4 contract — mirror build_qwen_argv):
        # the resolved binary is empty/whitespace. Refuse with a typed
        # ValueError naming the harness — no filesystem existence check,
        # the adapter is a pure string/list builder (no I/O).
        raise ValueError(
            "goose binary is not configured "
            "(empty GOOSE_BIN / [goose] bin)"
        )
    parts += ["run", "--no-session", "-q", "--max-turns", "1"]
    if task:
        parts += ["-t", task]
    return parts


def build_goose_env(model_target=None, cfg=None) -> dict:
    """The child-env override dict for a one-shot Goose invocation.

    Goose's OpenAI-compatible provider reads env-based configuration, so
    the dict carries the BOUND provider surface (measured against
    goose 1.47.0 — ``goose run`` reads ``GOOSE_PROVIDER``, ``GOOSE_MODEL``,
    ``OPENAI_BASE_URL``/``OPENAI_HOST``, ``OPENAI_API_KEY``):

    - ``GOOSE_PROVIDER`` — bound literal ``"openai"`` (selects the
      OpenAI-compatible provider; the handoff D1 binds an OpenAI-
      compatible provider pointed at the ``/v1`` endpoint — the
      ``opencode`` lesson).
    - ``GOOSE_MODEL`` — the resolved model target VERBATIM. OMITTED when
      empty (the env var name is ``GOOSE_MODEL``, NOT ``OPENAI_MODEL``
      — goose rejects "No model configured" when only ``OPENAI_MODEL``
      is set; verified by hand against ``goose run --debug``).
    - ``OPENAI_BASE_URL`` — the resolved base URL FORCED to end in
      ``/v1`` (appended when non-empty and not already ending in it).
      OMITTED when the base URL is empty (goose's default endpoint).
    - ``OPENAI_API_KEY`` — the VALUE read from the environment variable
      NAMED by ``cfg.get_goose_api_key_env()`` (the config value is a
      NAME, never a secret). OMITTED when the name is empty or the named
      variable is unset / empty.

    Returns an empty dict when there is nothing to set (no provider, no
    base URL, no model, no key) — the caller can then fall back to
    inheriting the parent env. This builder never reads or returns a
    secret value of its own; the key is read from the NAMED environment
    variable the config identifies. The XDG / HOME state-dir relocation
    that goose requires on a read-only HOME is the invoke layer's job
    (the adapter does NOT set HOME / XDG_* — it stays a pure env
    carrier for the provider surface).
    """
    if cfg is None:
        cfg = config
    env: dict = {}
    base_url = (cfg.get_goose_base_url() or "").strip()
    name = (cfg.get_goose_api_key_env() or "").strip()
    # Provider: bound literal for the OpenAI-compatible provider (the
    # handoff's D1 binding). Set only when an OpenAI-specific knob is
    # configured (base_url or api_key_env). The model alone does NOT
    # imply OpenAI-compatible (the model can travel under any provider);
    # so when neither base_url nor api_key is wired, GOOSE_PROVIDER is
    # omitted and the caller inherits goose's default — this is what
    # makes ``build_goose_env()`` return ``{}`` when nothing is
    # configured (the qwen contract the tests guard).
    if base_url or name:
        env["GOOSE_PROVIDER"] = "openai"
    if base_url:
        if not base_url.endswith("/v1"):
            base_url = base_url.rstrip("/") + "/v1"
        env["OPENAI_BASE_URL"] = base_url
    model = model_target_identity(model_target)
    if model:
        env["GOOSE_MODEL"] = model
    if name:
        import os
        key = os.environ.get(name)
        if key:
            env["OPENAI_API_KEY"] = key
    return env



def build_sweagent_invocation(model_target=None, task=None, cfg=None) -> str:
    """The one-shot SWE-agent invocation as a shell command string.

    ``[<sweagent_bin>, run, --agent.model.name <model>, --env.repo.path <path>,
    --problem_statement.text <task>]``. ``model_target`` is rendered into
    ``--agent.model.name`` here (SWE-agent takes the model as a CLI flag —
    DIFFERENT from qwen/goose, where the model travels in the child env).
    ``task`` is appended via ``--problem_statement.text`` when supplied;
    the launch form omits ``--problem_statement.text`` (and likewise omits
    ``--env.repo.path`` if no repo is configured).
    """
    return _join_argv(build_sweagent_argv(model_target, task, cfg))


def build_sweagent_argv(model_target=None, task=None, cfg=None) -> list:
    """The one-shot SWE-agent invocation as an argv list.

    ``[<sweagent_bin>, run, --agent.model.name <model>?, --env.repo.path <path>?,
    --problem_statement.text <task>?]``.

    Bound against the installed sweagent build 1.1.0 (see ``sweagent run
    --help``, ``sweagent --help``, ``sweagent run --help_option
    sweagent.agent.problem_statement.TextProblemStatement``):

    - ``run`` is the one-shot subcommand — "Run swe-agent on a single problem
      statement, for example a github issue" (``sweagent run --help``).
    - ``--agent.model.name <model>`` — the model flag (CLI, NOT env —
      measured against ``sweagent run --help``).
    - ``--env.repo.path <path>`` — the local-repo anchor for the
      ``LocalRepoConfig`` deployment; OMITTED when empty (then sweagent
      defaults to its other repo forms — github / pre-existing).
    - ``--problem_statement.text <task>`` — the inline-text problem
      statement for the ``TextProblemStatement`` form; OMITTED when
      empty (then the launch form takes over).
    - ``--config config/default.yaml`` is NOT emitted by this adapter:
      sweagent auto-loads the default config from
      ``SWE_AGENT_CONFIG_DIR/config/default.yaml`` when no
      ``--config`` is supplied (measured from
      "Loading default config from …/config/default.yaml, because no
      other config file is specified.").

    The complete ``task`` (any size, any embedded newlines) is one argv
    element — no shlex round-trip, no shell interpolation. bin from
    ``cfg.get_sweagent_bin()``; missing/empty bin raises a typed
    ``ValueError`` naming ``sweagent`` (pure string builder, NO
    filesystem existence check) — the refusal surfaces BEFORE any
    subprocess.
    """
    # Experimental gate (D3 — Run 027 / HA-4): refuse BEFORE the missing-
    # binary check so a disabled experimental harness always refuses with
    # the gate error — deterministic regardless of binary state. Pure
    # string/list-builder refusal, no I/O, no subprocess.
    _require_experimental_enabled("sweagent", cfg)
    if cfg is None:
        cfg = config
    parts = shlex.split(cfg.get_sweagent_bin())
    if not parts:
        # Missing-binary refusal (D1 / D4 contract — mirror build_qwen_argv /
        # build_goose_argv): the resolved binary is empty/whitespace. Refuse
        # with a typed ValueError naming the harness — no filesystem
        # existence check, the adapter is a pure string/list builder (no I/O).
        raise ValueError(
            "sweagent binary is not configured "
            "(empty SWEAGENT_BIN / [sweagent] bin)"
        )
    parts += ["run"]
    model = model_target_identity(model_target)
    if model:
        parts += ["--agent.model.name", model]
    repo_path = (cfg.get_sweagent_repo_path() or "").strip()
    if repo_path:
        parts += ["--env.repo.path", repo_path]
    if task:
        parts += ["--problem_statement.text", task]
    return parts


def build_sweagent_env(model_target=None, cfg=None) -> dict:
    """The child-env override dict for a one-shot SWE-agent invocation.

    Unlike qwen / goose (which return ``{}`` when nothing is wired),
    this builder ALWAYS returns the three REQUIRED SWE-agent directory
    keys — they are LOAD-BEARING for the installed v1.1.0 git checkout
    (the bare ``sweagent --version`` ASSERTS on ``CONFIG_DIR.is_dir()``;
    see the env-binding correction in the sweagent install record).
    The cfg getters all default to ``str(Path.home() / "tools" /
    "SWE-agent" / ...)`` so a vanilla user (no env, no ini) still gets a
    usable env; the user can override via env or ini when running
    elsewhere:

    - ``SWE_AGENT_CONFIG_DIR``   — from ``cfg.get_sweagent_config_dir()``
    - ``SWE_AGENT_TOOLS_DIR``    — from ``cfg.get_sweagent_tools_dir()``
    - ``SWE_AGENT_TRAJECTORY_DIR`` — from ``cfg.get_sweagent_trajectory_dir()``

    Each value is stripped; an empty value short-circuits to the home-
    relative default so the bare-CLI assertion never fires through this
    builder. ``model_target`` is accepted for call-shape parity with
    qwen/goose but is intentionally NOT used (the SWE-agent model is a
    CLI flag — ``--agent.model.name`` — not an env var; mirroring the
    qwen/goose builder signatures keeps the invoke layer clean).
    """
    if cfg is None:
        cfg = config
    config_dir = (cfg.get_sweagent_config_dir() or "").strip()
    if not config_dir:
        config_dir = str(Path.home() / "tools" / "SWE-agent" / "config")
    tools_dir = (cfg.get_sweagent_tools_dir() or "").strip()
    if not tools_dir:
        tools_dir = str(Path.home() / "tools" / "SWE-agent" / "tools")
    trajectory_dir = (cfg.get_sweagent_trajectory_dir() or "").strip()
    if not trajectory_dir:
        trajectory_dir = str(Path.home() / "tools" / "SWE-agent" / "trajectories")
    return {
        "SWE_AGENT_CONFIG_DIR": config_dir,
        "SWE_AGENT_TOOLS_DIR": tools_dir,
        "SWE_AGENT_TRAJECTORY_DIR": trajectory_dir,
    }




def build_aider_invocation(model_target=None, task=None, cfg=None) -> str:
    """The headless one-shot Aider invocation as a shell command string.

    ``[<aider_bin>, --yes-always, --no-auto-commits,
    --no-dirty-commits, --model <model>?, --message <task>?]``. The non-interactive +
    no-auto-commit binding (``--yes-always``, ``--no-auto-commits``,
    ``--no-dirty-commits``) is ALWAYS present — aider commits by default
    (the git-policy stays with the Human — run 027 / HA-4 §2). The
    ``--model`` flag carries the model verbatim (aider takes the model
    as a CLI flag, like sweagent — DIFFERENT from qwen/goose where the
    model travels in the child env). ``task`` is appended via
    ``--message`` when supplied; the launch form omits ``--message``.
    """
    return _join_argv(build_aider_argv(model_target, task, cfg))


def build_aider_argv(model_target=None, task=None, cfg=None) -> list:
    """The headless one-shot Aider invocation as an argv list.

    ``[<aider_bin>, --yes-always, --no-auto-commits,
    --no-dirty-commits, --model <model>?, --message <task>?]``.

    Bound against the installed aider 0.86.2 build (see ``aider --help``,
    ``aider --version``):

    - ``--model <model>`` — the model flag (CLI, NOT env — measured against
      ``aider --help``; env AIDER_MODEL is honored as fallback only, the
      CLI flag wins).
    - ``--yes-always`` — always answer yes to every yes/no prompt (the
      headless non-interactive surface).
    - ``--no-auto-commits`` — REQUIRED. aider commits by itself (default
      True); this disables every auto-commit. The git-policy stays with
      the Human (run 027 / HA-4 §2).
    - ``--no-dirty-commits`` — REQUIRED. aider commits even when the repo
      is dirty by default (AIDER_DIRTY_COMMITS); this disables that too
      so a dirty work-tree pauses instead of committing thrash.
    - ``--no-git-commit-verify`` / ``--no-git`` — present in ``aider
      --help`` but NOT emitted by this adapter (the no-auto-commit trio
      already covers the bind-points for run 027 / HA-4).
    - ``--message <task>`` — the one-shot task flag (``aider --help``
      documents the aliases ``--msg`` and ``-m`` as well: ``--message
      COMMAND, --msg COMMAND, -m COMMAND``); the adapter DELIBERATELY
      emits the long ``--message`` form for readability, appended only
      when a task is supplied (the launch form omits it).

    The complete ``task`` (any size, any embedded newlines) is one argv
    element — no shlex round-trip, no shell interpolation. bin from
    ``cfg.get_aider_bin()``; missing/empty bin raises a typed
    ``ValueError`` naming ``aider`` (pure string builder, NO filesystem
    existence check) — the refusal surfaces BEFORE any subprocess.
    """
    # Experimental gate (D3 — Run 027 / HA-4): refuse BEFORE the missing-
    # binary check so a disabled experimental harness always refuses with
    # the gate error — deterministic regardless of binary state. Pure
    # string/list-builder refusal, no I/O, no subprocess.
    _require_experimental_enabled("aider", cfg)
    if cfg is None:
        cfg = config
    parts = shlex.split(cfg.get_aider_bin())
    if not parts:
        # Missing-binary refusal (D1 / D4 contract — mirror build_qwen_argv /
        # build_goose_argv / build_sweagent_argv): the resolved binary is
        # empty/whitespace. Refuse with a typed ValueError naming the
        # harness — no filesystem existence check, the adapter is a pure
        # string/list builder (no I/O).
        raise ValueError(
            "aider binary is not configured "
            "(empty AIDER_BIN / [aider] bin)"
        )
    parts += ["--yes-always", "--no-auto-commits", "--no-dirty-commits"]
    model = model_target_identity(model_target)
    if model:
        parts += ["--model", model]
    if task:
        parts += ["--message", task]
    return parts


def build_aider_env(model_target=None, cfg=None) -> dict:
    """The child-env override dict for a headless Aider invocation.

    Aider is special-cased: it has NO load-bearing child-env override.
    The model is a CLI flag (``--model <model>``, set by
    ``build_aider_argv``); the API key is inherited from the parent
    environment (the allocator passes ``env=None`` / ``inherit`` when
    ``build_aider_env`` returns ``{}``). This mirrors the qwen / goose
    "return ``{}`` when nothing is wired" contract — the invoke layer's
    env-threading block uses ``env = {**os.environ, **aider_env} if
    aider_env else None`` so this builder's empty-dict result falls
    through to ``env=None`` (inherit) byte-identical to the existing
    harnesses.

    Unlike sweagent (where the three SWE_AGENT_*_DIR env vars are
    LOAD-BEARING and the builder ALWAYS returns them), this builder
    ALWAYS returns ``{}`` — the empty dict is the entire binder. ``cfg``
    and ``model_target`` are accepted for call-shape parity with the
    other build_*_env builders; neither is consulted here.
    """
    return {}


def _require_experimental_enabled(harness, cfg):
    """Refuse (typed ValueError) when ``harness`` is experimental but not enabled.

    D3 experimental gate (Run 027 / HA-4): the experimental set
    (``EXPERIMENTAL_HARNESSES`` in ``capabilities.py``) is REGISTERED in
    the adapter surface but NOT exposed as a default. A call to
    ``build_sweagent_argv`` or ``build_aider_argv`` MUST refuse with a
    typed ``ValueError`` BEFORE any subprocess when the harness is in
    ``EXPERIMENTAL_HARNESSES`` but NOT in the user's
    ``[experimental] enabled_harnesses`` set.

    ``cfg`` defaults to the package's own ``harness_allocator.config``
    so a vanilla invocation gets the same refusal. The check is purely
    declarative: ``cfg.get_experimental_enabled_harnesses()`` returns
    a set of stripped harness keys (the env/ini split idiom used by
    ``get_codex_add_dirs`` / ``get_qwen_add_dirs``); empty set (default)
    means "no experimental harness is enabled", so the very first call
    to ``build_sweagent_argv`` / ``build_aider_argv`` in a vanilla
    session refuses.

    Pure string/list-builder refusal — no I/O, no subprocess, no
    filesystem check. ``ValueError`` (a typed runtime error caught by
    the invoke layer's existing ``except Exception`` / ``except
    ValueError`` clause).
    """
    if harness not in EXPERIMENTAL_HARNESSES:
        return
    if cfg is None:
        cfg = config
    enabled = cfg.get_experimental_enabled_harnesses()
    if harness not in enabled:
        raise ValueError(
            f"experimental harness {harness!r} is not enabled; "
            f"add it to [experimental] enabled_harnesses "
            f"(env EXPERIMENTAL_ENABLED_HARNESSES)"
        )




def _codex_command(model_target, cfg) -> str:
    """``codex -m <model_target>`` — the resolved target passed through verbatim."""
    return _join_argv(_codex_argv(model_target, cfg))


def _codex_argv(model_target, cfg) -> list:
    """``[codex, -m, <model_target>?, -C <workdir>?, --add-dir <dir>*, --sandbox
    <mode>, --ask-for-approval <policy>]`` — argv list for subprocess.Popen.

    Codex's provider is configured at the user level (its catalog), which the
    allocator deliberately does not duplicate. Rendering the caller-supplied
    target here is expression, not selection: the identity came from Model
    Allocator and is neither looked up nor defaulted.

    The workspace/access flags (``-C``, ``--add-dir``, ``--sandbox``,
    ``--ask-for-approval``) all come from ``cfg`` and are omitted when that
    config returns empty values, so a caller that configures none of them keeps
    the historical ``codex -m <model>`` shape byte-for-byte.

    Profile selector (Run 024 / D1): when ``cfg.get_codex_profile()`` returns
    ``"gpu"`` the sandbox mode is overridden to ``get_codex_profile_gpu_sandbox()``
    (default ``danger-full-access``) and the profile's add-dirs are APPENDED
    after the base add-dirs as ``--add-dir <dir>``. Empty / absent / any other
    profile value leaves the launch byte-identical to today. The profile is
    read DEFENSIVELY (``getattr`` + ``lambda: ""``) so the existing
    ``_FakeCfg`` test double in tests/test_harness_allocator.py (which has no
    ``get_codex_profile`` method) keeps working.
    """
    parts = [cfg.get_codex_bin()]
    model = model_target_identity(model_target)
    if model:
        parts += ["-m", model]

    workdir = (cfg.get_codex_workdir() or "").strip()
    if workdir:
        parts += ["-C", workdir]

    for d in cfg.get_codex_add_dirs():
        d = (d or "").strip()
        if d:
            parts += ["--add-dir", d]

    # Profile selector (D1). An empty/absent profile keeps today's launch
    # shape byte-for-byte — the ratchet the 272 existing HA-1 tests pin.
    profile = (
        getattr(cfg, "get_codex_profile", lambda: "")() or ""
    ).strip()

    if profile == "gpu":
        # Sandbox override (the gpu signal — danger-full-access by default).
        parts += ["--sandbox", cfg.get_codex_profile_gpu_sandbox()]
        # Append the gpu add-dirs AFTER the base loop.
        for d in cfg.get_codex_profile_gpu_add_dirs():
            d = (d or "").strip()
            if d:
                parts += ["--add-dir", d]
    else:
        # Profile-less path: today's behaviour, byte-identical.
        parts += ["--sandbox", cfg.get_codex_sandbox()]

    parts += ["--ask-for-approval", cfg.get_codex_ask_for_approval()]
    return parts


def _join_argv(argv) -> str:
    """Render an argv list as a shell-quoted command string for display/log."""
    return " ".join(shlex.quote(part) for part in argv)


# ── MCP-Light builders (Run 004 / Objective A) ──────────────────────
#
# These are PURE string/list builders — no process spawning, no I/O. The
# existing module contract (HarnessAdapter — builds the shell command or argv
# that launches a harness) is preserved: callers decide WHEN to run the
# Codex MCP registration command (live wiring is handoff 019, TG12/TG13) and
# how to merge the DSH MCP patch overlay into the composed patch tree (handoff
# 019 or a future caller). The builders only render the right SYNTAX.
#
# IMPORTANT (TG4 / TG1): _codex_argv and build_dsh_argv are UNCHANGED. MCP is
# OFF by default, and the default config returns no servers, so existing argv
# shapes are byte-identical when MCP is off.


def build_codex_mcp_setup_argv(name, url):
    """The argv Harness Allocator WOULD run to register a Codex MCP server.

    Returns ``['codex', 'mcp', 'add', name, '--url', url]`` — the streamable-
    http shape verified against Codex CLI 0.148.0 (`codex mcp add --help`).
    This is NOT part of the codex launch argv; calling it does NOT modify
    state in this handoff (live ``codex mcp add`` against ``~/.codex/config.toml``
    is handoff 019, TG12).
    """
    if not name or not url:
        raise ValueError("build_codex_mcp_setup_argv requires non-empty name and url")
    return ["codex", "mcp", "add", name, "--url", url]


def _render_dsh_mcp_entry(name, transport, url_or_cmd, required):
    """Render one @deepseek-ai/dsh-mcp-client plugin entry as a YAML string.

    Shape verified from @deepseek-ai/dsh-mcp-client README (v0.1.0-rc.7):

        - id: <name>
          name: '@deepseek-ai/dsh-mcp-client'
          config:
            serverName: <name>
            transport: <streamable-http|stdio>
            url: <url>          # streamable-http
            # command: <cmd>    # stdio (future; not in this run)
            failOnStartupError: <true|false>

    ``failOnStartupError`` is the plugin's native "required" switch — when
    true, it rejects plugin activation on initial connection or tool-sync
    failure. That is how TG5 maps onto the plugin.
    """
    if transport == "streamable-http":
        body = (
            f"  - id: {name}\n"
            f"    name: '@deepseek-ai/dsh-mcp-client'\n"
            f"    config:\n"
            f"      serverName: {name}\n"
            f"      transport: streamable-http\n"
            f"      url: {url_or_cmd}\n"
            f"      failOnStartupError: {'true' if required else 'false'}\n"
        )
    elif transport == "stdio":
        body = (
            f"  - id: {name}\n"
            f"    name: '@deepseek-ai/dsh-mcp-client'\n"
            f"    config:\n"
            f"      serverName: {name}\n"
            f"      transport: stdio\n"
            f"      command: {url_or_cmd}\n"
            f"      failOnStartupError: {'true' if required else 'false'}\n"
        )
    else:
        raise ValueError(f"unsupported DSH MCP transport: {transport!r}")
    return body


def build_dsh_mcp_patch_yml(servers, required=False):
    """The DSH MCP patch overlay as a YAML string.

    A list of ``(name, transport, url_or_cmd)`` triples (matching
    :func:`harness_allocator.config.get_dsh_mcp_servers`) renders as one
    ``@deepseek-ai/dsh-mcp-client`` plugin entry per server. When
    ``required=True`` every entry sets ``failOnStartupError: true`` (TG5 maps
    onto the plugin's native required switch); otherwise every entry sets it
    to ``false`` (the plugin default).

    Returns the empty string when ``servers`` is empty — callers compose this
    overlay with the existing ``dsh --patch`` overlay(s), and an empty string
    means "no MCP layer added" (TG4).
    """
    if not servers:
        return ""
    head = (
        "# Auto-generated by harness_allocator.adapter.build_dsh_mcp_patch_yml\n"
        "# Compose with existing --patch overlays (this is a cordis.patch.yml-shaped\n"
        "# list of plugin entries). One @deepseek-ai/dsh-mcp-client entry per MCP server.\n"
        "- entries:\n"
    )
    body = "".join(_render_dsh_mcp_entry(n, t, u, required) for (n, t, u) in servers)
    return head + body


def _default_reachability_check(url, timeout=2.0):
    """Best-effort reachability probe for a streamable-http MCP endpoint.

    Returns True if the endpoint answers an HTTP request within ``timeout``
    seconds, False otherwise. The probe accepts ANY HTTP response (even 4xx)
    as "the server is up" — MCP servers answer the streamable-http handshake
    with a 400 to bare GETs (mcp-light verified behaviour), so a connection-
    refused / timeout is the only signal we treat as "not reachable".

    This is the default reachability check used by
    :func:`validate_mcp_required`. Tests inject a stub via the
    ``reachability`` parameter so the validator is unit-testable without
    network.
    """
    try:
        import socket
        from urllib.parse import urlparse
    except ImportError:  # pragma: no cover — stdlib always present
        return False
    parsed = urlparse(url)
    host = parsed.hostname
    port = parsed.port
    if not host or not port:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def validate_mcp_required(codex_servers, codex_required, dsh_servers, dsh_required,
                          reachability=None):
    """Raise a deterministic error when MCP is required but cannot be satisfied (TG5).

    Rules:

    - When both MCP variants are OPTIONAL, this helper NEVER raises.
    - When MCP is REQUIRED and NO servers are configured -> ``ValueError``
      naming the missing harness variant.
    - When MCP is REQUIRED and a configured server is NOT reachable (probe
      fails) -> ``ValueError`` naming the failing server. The probe is
      injectable: pass ``reachability=callable`` in tests; default is a TCP
      socket connect to the URL's host:port.

    This keeps the existing default (MCP off, ``required=False``, no servers)
    a no-op (TG4) and lets the harness caller ask for a clear, deterministic
    failure when MCP is mandatory.
    """
    probe = reachability if reachability is not None else _default_reachability_check
    if codex_required:
        if not codex_servers:
            raise ValueError("Codex MCP is required but no servers are configured")
        for name, url in codex_servers:
            if not probe(url):
                raise ValueError(f"Codex MCP server {name!r} unreachable at {url}")
    if dsh_required:
        if not dsh_servers:
            raise ValueError("DSH MCP is required but no servers are configured")
        for name, _transport, url_or_cmd in dsh_servers:
            # Only streamable-http servers have a URL we can probe; stdio servers
            # are validated by the plugin at startup (failOnStartupError), so we
            # leave them alone here.
            if url_or_cmd.startswith(("http://", "https://")) and not probe(url_or_cmd):
                raise ValueError(f"DSH MCP server {name!r} unreachable at {url_or_cmd}")
    return None
