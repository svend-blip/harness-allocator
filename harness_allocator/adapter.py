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

import shlex

from . import config
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
