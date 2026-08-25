"""Configuration surface for the Harness Allocator.

Single source of truth for the harness launcher paths, profiles and patch
overlays this package owns. Independent of any orchestrator: it reads only
environment variables and an optional ``harness-allocator.ini`` next to the
project root, never another project's config module.

Sources (in priority order):

1. Environment variables (secrets, infrastructure)
2. ``harness-allocator.ini`` ``[harness]`` section (app-config defaults)
3. Hardcoded fallbacks (last resort, for development only)

There is no ``.env`` loader here: credentials are supplied by the process
environment, so a harness invoked through this package inherits them the way
its own CLI expects.
"""

from __future__ import annotations

import configparser
import os
import tempfile
from pathlib import Path

#: The ini next to the project root. Optional — env vars cover the defaults.
_INI_PATH = Path(__file__).resolve().parent.parent / "harness-allocator.ini"

_config = configparser.ConfigParser()
if _INI_PATH.exists():
    _config.read(_INI_PATH, encoding="utf-8")


def _ini(section, key, fallback=None):
    return _config.get(section, key, fallback=fallback)


def get_codex_bin() -> str:
    """Codex CLI launcher. Env ``CODEX_BIN``, ini ``[harness] codex_bin``, or ``codex``."""
    env = os.environ.get("CODEX_BIN")
    if env:
        return env
    configured = _ini("harness", "codex_bin")
    return configured or "codex"


def get_codex_workdir() -> str:
    """Codex ``-C`` working root. Env ``CODEX_WORKDIR``, ini ``[harness] codex_workdir``, or empty.

    Empty means "use the caller's working directory" (no ``-C`` flag), which is
    the historical behaviour. This package never invents a workdir for a role.
    """
    env = os.environ.get("CODEX_WORKDIR")
    if env:
        return env
    configured = _ini("harness", "codex_workdir")
    return configured or ""


def get_codex_add_dirs() -> list:
    """Codex ``--add-dir`` paths. Env ``CODEX_ADD_DIRS`` (colon/comma-separated) or ini ``[harness] codex_add_dirs``.

    Defaults to the OS temp dir only — a generic, orchestrator-independent
    location every coding harness needs for builds/tools. This package does not
    know the caller's bridge/project directories; the caller supplies those
    through its own config when it delegates here. The temp dir is resolved via
    ``tempfile.gettempdir()`` (respects ``TMPDIR``), never hardcoded.
    """
    env = os.environ.get("CODEX_ADD_DIRS")
    raw = env or _ini("harness", "codex_add_dirs") or ""
    if raw:
        return [p.strip() for p in raw.replace(",", ":").split(":") if p.strip()]
    return [tempfile.gettempdir()]


def get_codex_sandbox() -> str:
    """Codex sandbox mode (``--sandbox``). Env ``CODEX_SANDBOX``, ini ``[harness] codex_sandbox``, or ``workspace-write``."""
    env = os.environ.get("CODEX_SANDBOX")
    if env:
        return env
    configured = _ini("harness", "codex_sandbox")
    return configured or "workspace-write"


def get_codex_ask_for_approval() -> str:
    """Codex approval policy (``--ask-for-approval``). Env ``CODEX_ASK_FOR_APPROVAL``, ini ``[harness] codex_ask_for_approval``, or ``never`` (autonomous)."""
    env = os.environ.get("CODEX_ASK_FOR_APPROVAL")
    if env:
        return env
    configured = _ini("harness", "codex_ask_for_approval")
    return configured or "never"


# ── Codex profile selector (Run 024 / D1) ────────────────────────────
#
# A PROFILE SELECTOR lets the implementer role (or any caller) pick a
# pre-baked launch shape without rewriting the adapter for each new mode.
# Today the only profile is ``gpu`` — it overrides the sandbox to
# ``danger-full-access`` (so the gpu-gated proofs can run) and APPENDS
# additional ``--add-dir`` flags after the base add-dirs.
#
# Precedence (env -> ini -> default) matches the rest of this module:
#
#   CODEX_PROFILE env (e.g. "gpu") wins. An empty/absent env falls through
#   to the ini ``[harness] codex_profile`` setting, then to ``""`` (the
#   profile-less path — today's launch shape, byte-for-byte unchanged).
#
# The profile-specific knobs live in ``[codex_profile_gpu]`` (sandbox,
# add_dirs). That section is OUTSIDE the run-024 §3 scope fence: it is the
# OVERRIDE channel callers may populate to tune gpu without code changes.
# Defaults here (``danger-full-access`` / ``[]``) carry the bound values
# when the ini section is absent, exactly like the other adapters.

def get_codex_profile() -> str:
    """Codex profile selector. Env ``CODEX_PROFILE``, ini ``[harness] codex_profile``, or ``""`` (profile-less).

    Empty (default) keeps the launch byte-identical to today's path. The
    only profile this run defines is ``gpu``; unknown non-empty values
    flow through the adapter without special handling — they are simply
    not the gpu profile, so the profile-less path is taken (the bound
    values are mode names, not arbitrary code).
    """
    env = os.environ.get("CODEX_PROFILE")
    if env and env.strip():
        return env.strip()
    configured = _ini("harness", "codex_profile", fallback="")
    return (configured or "").strip()


def get_codex_profile_gpu_sandbox() -> str:
    """Sandbox mode the gpu profile renders. Ini ``[codex_profile_gpu] sandbox`` or ``danger-full-access``.

    ``danger-full-access`` is the human-decided value (GOAL.md §1 D1 DECISION
    FOR THE HUMAN, bound in RUN-LEDGER) for the gpu profile — gpu-gated
    proofs need to escape the sandbox. Other profiles are free to override
    this via the ini section.
    """
    configured = _ini("codex_profile_gpu", "sandbox", fallback="")
    return (configured or "").strip() or "danger-full-access"


def get_codex_profile_gpu_add_dirs() -> list:
    """Add-dir paths the gpu profile appends after the base add-dirs. Ini ``[codex_profile_gpu] add_dirs`` or ``[]``.

    Same colon/comma split idiom as :func:`get_codex_add_dirs`: env-empty
    here (this knob is ini-only — values travel via the override channel),
    ini ``[codex_profile_gpu] add_dirs``, default ``[]``. Each non-empty
    entry becomes one ``--add-dir <dir>`` AFTER the base add-dirs loop.
    """
    raw = _ini("codex_profile_gpu", "add_dirs", fallback="")
    if raw:
        return [p.strip() for p in raw.replace(",", ":").split(":") if p.strip()]
    return []


def get_codex_fresh_context_policy() -> str:
    """Codex fresh-context policy at a governed work-unit boundary.

    Env ``CODEX_FRESH_CONTEXT_POLICY`` takes precedence over the optional
    ``[harness] codex_fresh_context_policy`` value. The supported values are
    ``"off"`` (the default) and ``"work_unit"``; unknown non-empty values fail
    loudly rather than silently disabling fresh context.
    """
    env = os.environ.get("CODEX_FRESH_CONTEXT_POLICY")
    if env and env.strip():
        policy = env.strip()
    else:
        configured = _ini("harness", "codex_fresh_context_policy", fallback="")
        policy = (configured or "").strip()
    if not policy:
        return "off"
    if policy not in ("off", "work_unit"):
        raise ValueError(
            "unsupported Codex fresh-context policy "
            f"{policy!r}; expected 'off' or 'work_unit'"
        )
    return policy


def get_dsh_bin() -> str:
    """DeepSeek Harness launcher. Env ``DSH_BIN``, ini ``[harness] dsh_bin``, or npx.

    The default is ``npx @deepseek-ai/dsh`` — the verified non-browser path —
    rather than a hardcoded absolute path, so the harness resolves on any
    machine that has the package installed or reachable via the registry.
    """
    env = os.environ.get("DSH_BIN")
    if env:
        return env
    configured = _ini("harness", "dsh_bin")
    return configured or "npx @deepseek-ai/dsh"


def get_dsh_profile() -> str:
    """DeepSeek Harness profile. Env ``DSH_PROFILE``, ini ``[harness] dsh_profile``, or ``headless``.

    ``headless`` is the one-shot profile: ``dsh --profile headless <task>``
    answers one task, prints the result, and exits. That matches the
    stateless-per-wakeup terminal design.
    """
    env = os.environ.get("DSH_PROFILE")
    if env:
        return env
    configured = _ini("harness", "dsh_profile")
    return configured or "headless"


def get_dsh_patch_path() -> str:
    """Path to the DeepSeek Harness patch overlay for the resolved model target.

    Env ``DSH_V4_PRO_PATCH``, ini ``[harness] dsh_v4_pro_patch``, or empty
    (no overlay). The patch is the caller's already-resolved model-target
    embodiment (Model Allocator sets the env/ini); this getter only passes it
    through. Empty means no overlay is pinned — this package never chooses a
    model, so it never substitutes a patch of its own.
    """
    env = os.environ.get("DSH_V4_PRO_PATCH")
    if env:
        return env
    configured = _ini("harness", "dsh_v4_pro_patch")
    return configured or ""



# ── Qwen Code adapter config (Run 022 / HA-2) ───────────────────────────
#
# Qwen Code's endpoint wiring is env-based, not flag-based: model and
# endpoint travel ONLY in the child env (see adapter.build_qwen_env), so
# config here exposes NAMES (bin, base_url, api_key_env, ...) — never
# secrets. Defaults mirror the existing per-harness pattern: every value
# has a documented default and an env override, no hardcoded host-absolute paths
# anywhere.


def get_qwen_bin() -> str:
    """Qwen Code launcher. Env ``QWEN_BIN``, ini ``[qwen] bin``, or ``qwen``.

    The default is the on-PATH ``qwen`` binary (verified installed
    host-side before the run opened). Like the other harnesses, callers
    can override with a full launcher path via env or ini.
    """
    env = os.environ.get("QWEN_BIN")
    if env:
        return env
    configured = _ini("qwen", "bin")
    return configured or "qwen"


def get_qwen_base_url() -> str:
    """Qwen Code endpoint base URL. Env ``QWEN_BASE_URL``, ini ``[qwen] base_url``, or empty.

    Empty (default) means Qwen Code's own endpoint — the allocator
    deliberately does NOT hardcode an endpoint. When non-empty, the adapter
    forces the value to end in ``/v1`` so it works with any OpenAI-compatible
    server (the opencode lesson — local endpoints need the ``/v1`` path).
    """
    env = os.environ.get("QWEN_BASE_URL")
    if env:
        return env
    configured = _ini("qwen", "base_url")
    return configured or ""


def get_qwen_api_key_env() -> str:
    """Name of the environment variable that holds the Qwen Code API key. Env ``QWEN_API_KEY_ENV``, ini ``[qwen] api_key_env``, or empty.

    The config value is a NAME — never the secret itself. The adapter reads
    the named variable from the environment when building the child env
    (see ``adapter.build_qwen_env``). Empty means "no key wired": the child
    env simply omits ``OPENAI_API_KEY`` and the runtime inherits the parent
    shell. This indirection lets callers route the key through whatever env
    var they already populate (e.g. ``QWEN_API_KEY``, ``OPENAI_API_KEY``).
    """
    env = os.environ.get("QWEN_API_KEY_ENV")
    if env:
        return env
    configured = _ini("qwen", "api_key_env")
    return configured or ""


def get_qwen_workdir() -> str:
    """Qwen Code working root. Env ``QWEN_WORKDIR``, ini ``[qwen] workdir``, or empty.

    Empty (default) means "use the caller's working directory" — Qwen Code
    has no explicit workdir flag, so this is reserved for callers who want to
    anchor the run via a documented knob.
    """
    env = os.environ.get("QWEN_WORKDIR")
    if env:
        return env
    configured = _ini("qwen", "workdir")
    return configured or ""


def get_qwen_add_dirs() -> list:
    """Qwen Code ``--include-directories`` paths. Env ``QWEN_ADD_DIRS`` (colon/comma-separated), ini ``[qwen] add_dirs``, or empty.

    Empty (default) means the flag is omitted entirely — Qwen Code inherits
    its native behaviour. Callers populate this when they want the headless
    run to have read access to project directories outside the working
    directory.
    """
    env = os.environ.get("QWEN_ADD_DIRS")
    raw = env or _ini("qwen", "add_dirs") or ""
    if raw:
        return [p.strip() for p in raw.replace(",", ":").split(":") if p.strip()]
    return []


def get_qwen_approval_mode() -> str:
    """Qwen Code approval mode (``--approval-mode``). Env ``QWEN_APPROVAL_MODE``, ini ``[qwen] approval_mode``, or ``yolo``.

    ``yolo`` is the headless non-interactive mode — matches the
    one-shot-by-design shape of the dsh adapter (no stdin waits, no
    confirmation prompts). Other values (e.g. ``auto-edit``, ``default``)
    are accepted as a config override; the value here is the literal string
    the adapter passes to ``--approval-mode``.
    """
    env = os.environ.get("QWEN_APPROVAL_MODE")
    if env:
        return env
    configured = _ini("qwen", "approval_mode")
    return configured or "yolo"


# ── Goose adapter config (Run 026 / HA-3) ────────────────────────────
#
# Goose (Block AI agent CLI, github block/goose) is the first EXTENSIBLE
# agent harness in the set — ``extensions`` (mcp / skills / custom_tools)
# is measured honestly in capabilities.py. Endpoint wiring is env-based,
# not flag-based (model/provider/endpoint/api key travel in the child env
# only — see ``adapter.build_goose_env``), so config here exposes NAMES
# (bin, base_url, api_key_env, ...) — never secrets.
#
# Defaults mirror the per-harness pattern: every value has a documented
# default and an env override, no hardcoded host-absolute paths anywhere.
# The default ``bin = "goose"`` matches the on-PATH launcher installed
# host-side at ``~/.local/bin/goose`` for this run (verified, 1.47.0).
#
# The ``add_dirs`` config key is reserved: goose's CLI has NO
# ``--include-directories`` flag (verified ``goose run --help``), so
# ``build_goose_argv`` does NOT emit any directory flag. Callers may
# still configure add_dirs for forward compatibility / recipe-mode wiring.


def get_goose_bin() -> str:
    """Goose launcher. Env ``GOOSE_BIN``, ini ``[goose] bin``, or ``goose``.

    The default is the on-PATH ``goose`` binary — the verified
    host-side install at ``~/.local/bin/goose`` (1.47.0) is reachable
    via PATH for the run's execute layer. Like the other harnesses,
    callers can override with a full launcher path via env or ini.
    """
    env = os.environ.get("GOOSE_BIN")
    if env:
        return env
    configured = _ini("goose", "bin")
    return configured or "goose"


def get_goose_base_url() -> str:
    """Goose endpoint base URL. Env ``GOOSE_BASE_URL``, ini ``[goose] base_url``, or empty.

    Empty (default) means Goose's own endpoint — the allocator
    deliberately does NOT hardcode an endpoint. When non-empty, the
    adapter forces the value to end in ``/v1`` so it works with any
    OpenAI-compatible server (the opencode lesson — local endpoints
    need the ``/v1`` path). Goose's OpenAI-compatible provider reads
    ``OPENAI_BASE_URL`` (verified by hand against ``goose run --debug``
    on the 1.47.0 build with a stub endpoint).
    """
    env = os.environ.get("GOOSE_BASE_URL")
    if env:
        return env
    configured = _ini("goose", "base_url")
    return configured or ""


def get_goose_api_key_env() -> str:
    """Name of the environment variable that holds the Goose API key. Env ``GOOSE_API_KEY_ENV``, ini ``[goose] api_key_env``, or empty.

    The config value is a NAME — never the secret itself. The adapter
    reads the named variable from the environment when building the child
    env (see ``adapter.build_goose_env``). Empty means "no key wired":
    the child env simply omits ``OPENAI_API_KEY`` and the runtime
    inherits the parent shell. This indirection lets callers route the
    key through whatever env var they already populate (e.g.
    ``GOOSE_API_KEY``, ``OPENAI_API_KEY``, or a vault-supplied name).
    """
    env = os.environ.get("GOOSE_API_KEY_ENV")
    if env:
        return env
    configured = _ini("goose", "api_key_env")
    return configured or ""


def get_goose_workdir() -> str:
    """Goose working root. Env ``GOOSE_WORKDIR``, ini ``[goose] workdir``, or empty.

    Empty (default) means "use the caller's working directory" — Goose
    has no explicit workdir flag in ``goose run --help``, so this is a
    reserved knob for callers who want to anchor the run via a
    documented config key. The adapter does NOT currently emit a flag
    for it (verified).
    """
    env = os.environ.get("GOOSE_WORKDIR")
    if env:
        return env
    configured = _ini("goose", "workdir")
    return configured or ""


def get_goose_add_dirs() -> list:
    """Goose additional-directories knob. Env ``GOOSE_ADD_DIRS`` (colon/comma-separated), ini ``[goose] add_dirs``, or empty.

    RESERVED config key — goose's ``goose run --help`` exposes NO
    ``--include-directories`` flag (the qwen / opencode adapters have
    it; goose does not). The adapter therefore does NOT emit any
    directory flag in argv, regardless of this config. The key exists
    so callers can declare intent without code changes when a future
    goose build or recipe-mode wiring gains a directory-inclusion
    feature. Empty (default) keeps the adapter a no-op for add_dirs.
    """
    env = os.environ.get("GOOSE_ADD_DIRS")
    raw = env or _ini("goose", "add_dirs") or ""
    if raw:
        return [p.strip() for p in raw.replace(",", ":").split(":") if p.strip()]
    return []


# ── MCP-Light capability surface (Run 004 / Objective A) ─────────────
#
# MCP-Light is a governed, OPTIONAL capability: defaults are empty/false so
# that existing argv shapes are byte-identical when MCP is off (TG4). Required
# vs optional is explicit (GOAL.md §4.2) — the getter returns a distinct bool,
# not a magic string.
#
# Format on the wire:
#   CODEX_MCP_SERVERS = "name1=url1,name2=url2"          (colon OR comma sep)
#   DSH_MCP_SERVERS    = "name=transport=url_or_cmd,..." (transport=streamable-http|stdio)
#
# Transport values are restricted to the two the plugin supports (see
# @deepseek-ai/dsh-mcp-client README): ``stdio`` and ``streamable-http``.


def _split_entries(raw):
    """Split MCP env/ini entries on commas only, stripping blanks and empties.

    Entries are comma-separated; each entry uses ``=`` internally
    (``name=url`` for Codex, ``name=transport=url_or_cmd`` for DSH). We must
    NOT split on ``:`` because URL values like ``http://host:port/mcp`` contain
    colons — splitting on ``:`` would shred them. Empty inputs yield ``[]``
    (TG4: default is "MCP off", not "MCP error").
    """
    if not raw:
        return []
    return [tok.strip() for tok in str(raw).split(",") if tok.strip()]


def get_codex_mcp_servers():
    """Codex MCP server list. Env ``CODEX_MCP_SERVERS``, ini ``[harness] codex_mcp_servers``, or empty.

    Each entry is a ``(name, url)`` tuple — Codex MCP only registers the
    streamable-http shape via ``codex mcp add <name> --url <url>`` (verified in
    Codex CLI 0.148.0). Empty (default) means MCP off for Codex (TG4).
    """
    raw = os.environ.get("CODEX_MCP_SERVERS")
    if raw is None:
        raw = _ini("harness", "codex_mcp_servers", fallback="")
    out = []
    for tok in _split_entries(raw):
        if "=" not in tok:
            continue
        name, url = tok.split("=", 1)
        name = name.strip()
        url = url.strip()
        if name and url:
            out.append((name, url))
    return out


def get_codex_mcp_required():
    """Whether Codex MCP is REQUIRED. Env ``CODEX_MCP_REQUIRED``, ini ``[harness] codex_mcp_required``, or False.

    ``True`` means "fail clearly if MCP cannot be configured or reached"
    (GOAL.md §4.2 / TG5). ``False`` (the default) means "MCP off when no
    servers are configured, silent when unreachable if servers ARE configured".
    """
    raw = os.environ.get("CODEX_MCP_REQUIRED")
    if raw is None:
        raw = _ini("harness", "codex_mcp_required", fallback="")
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def get_dsh_mcp_servers():
    """DeepSeek Harness MCP server list. Env ``DSH_MCP_SERVERS``, ini ``[harness] dsh_mcp_servers``, or empty.

    Each entry is a ``(name, transport, url_or_cmd)`` tuple. For
    ``streamable-http`` the third element is the URL; for ``stdio`` it is the
    command string (the plugin's ``command`` field, args/env live in a future
    extension — see TG2/TG3 scope). Empty (default) means MCP off for DSH
    (TG4).
    """
    raw = os.environ.get("DSH_MCP_SERVERS")
    if raw is None:
        raw = _ini("harness", "dsh_mcp_servers", fallback="")
    out = []
    for tok in _split_entries(raw):
        parts = tok.split("=")
        if len(parts) != 3:
            continue
        name, transport, url_or_cmd = (p.strip() for p in parts)
        if transport not in ("stdio", "streamable-http"):
            continue
        if name and url_or_cmd:
            out.append((name, transport, url_or_cmd))
    return out


def get_dsh_mcp_required():
    """Whether DSH MCP is REQUIRED. Env ``DSH_MCP_REQUIRED``, ini ``[harness] dsh_mcp_required``, or False.

    ``True`` maps to the plugin's native ``failOnStartupError: true`` switch
    (see @deepseek-ai/dsh-mcp-client README) AND to the validator's raise
    behaviour (TG5). ``False`` (default) keeps the plugin's failOnStartupError
    false.
    """
    raw = os.environ.get("DSH_MCP_REQUIRED")
    if raw is None:
        raw = _ini("harness", "dsh_mcp_required", fallback="")
    return str(raw).strip().lower() in ("1", "true", "yes", "on")
