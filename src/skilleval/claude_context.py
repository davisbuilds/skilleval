"""Native Claude context controls and an observed-init gate for the VM executor.

This is a host client configuration, not an OS sandbox for Claude Code itself.
The caller owns the native binary identity, private host work directory, MCP
server configuration and VM boundary. No model invocation happens in this module.

Reviewed controls: https://code.claude.com/docs/en/settings-reference,
https://code.claude.com/docs/en/env-vars and /docs/en/cli-reference.
Managed sources: /docs/en/managed-settings and /docs/en/server-managed-settings.
"""

from __future__ import annotations

import json
import os
import pwd
import subprocess
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any, NoReturn

from skilleval.errors import SkillEvalError
from skilleval.harnesses.claude import CLAUDE_MODEL

VM_TOOL = "mcp__vm__execute"
_ALLOWED_TOOLS = {VM_TOOL, "EndConversation"}
# Exact vendor component identities observed on native Claude Code 2.1.321.
# Built-in mods survive disableAllHooks. Keep the vendor privacy guard; exclude
# foreign plugins by origin/path as well as name, and require no loaded skills.
# https://code.claude.com/docs/en/plugins/mods/overview#mods-built-into-claude-code
_BUILTIN_PLUGINS = {
    ("cc-plugin-agents-md", "builtin", "cc-plugin-agents-md@builtin"),
    ("cc-plugin-privacy", "builtin", "cc-plugin-privacy@builtin"),
    ("cc-plugin-plugin-authoring", "builtin", "cc-plugin-plugin-authoring@builtin"),
}
_BUILTIN_AGENTS = {"Explore", "general-purpose", "Plan", "statusline-setup"}
_SYSTEM_MANAGED_ROOTS = (Path("/Library/Application Support/ClaudeCode"), Path("/etc/claude-code"))
_MANAGED_PREFERENCES_ROOT = Path("/Library/Managed Preferences")
_SYSTEM_PREFERENCES_ROOT = Path("/Library/Preferences")
_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
_ENVIRONMENT = {
    "PATH": _PATH,
    "LANG": "en_US.UTF-8",
    "LC_ALL": "en_US.UTF-8",
    "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
    "CLAUDE_CODE_DISABLE_ATTACHMENTS": "1",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
    "CLAUDE_CODE_DISABLE_POLICY_SKILLS": "1",
    "CLAUDE_CODE_DISABLE_WORKFLOWS": "1",
    "CLAUDE_CODE_DISABLE_BUNDLED_SKILLS": "1",
    "CLAUDE_CODE_DISABLE_AGENT_VIEW": "1",
    "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
    "CLAUDE_CODE_DISABLE_CRON": "1",
    "CLAUDE_CODE_DISABLE_ARTIFACT": "1",
    "CLAUDE_CODE_DISABLE_REFUSAL_FALLBACK": "1",
    "CLAUDE_CODE_DISABLE_TERMINAL_TITLE": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    "CLAUDE_CODE_DISABLE_OFFICIAL_MARKETPLACE_AUTOINSTALL": "1",
    "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
    "ENABLE_TOOL_SEARCH": "false",
}


def _settings() -> dict[str, Any]:
    return {
        "disableAllHooks": True,
        "autoMemoryEnabled": False,
        "syncClaudeAiSkills": False,
        "syncClaudeAiPlugins": False,
        "disableClaudeAiConnectors": True,
        "disableBundledSkills": True,
        "disableSkillShellExecution": True,
        "disableWorkflows": True,
        # This key is managed-only; freshness of the plugin root and omitted
        # non-managed settings, rather than this key, exclude command plugins.
        "disableCommandPluginSources": True,
        "pluginConfigs": {
            "cc-plugin-agents-md@builtin": {"options": {"instructionFiles": "managed-only"}}
        },
        "permissions": {"allow": [VM_TOOL], "denyInsteadOfPrompting": True},
    }


def _private_json(destination: Path, payload: dict[str, Any]) -> None:
    """Never follow or overwrite an existing path, including a dangling symlink."""
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(payload, output, sort_keys=True, allow_nan=False)
        output.write("\n")


def _native_identity() -> pwd.struct_passwd:
    return pwd.getpwuid(os.getuid())


def _absolute(*paths: Path) -> None:
    if any(not path.is_absolute() for path in paths):
        raise SkillEvalError("native Claude context requires absolute paths")


def launch_context(
    binary: Path, work: Path, settings_path: Path, mcp_path: Path, plugin_root: Path
) -> tuple[list[str], dict[str, str]]:
    """Create fresh private settings/plugin state; return stdin-prompt argv/env.

    ``work`` and the settings parent must already exist in an invocation-private
    host directory. ``plugin_root`` and ``settings_path`` must not exist. MCP file
    contents belong to the caller and can be written after the auth preflight.
    HOME is the native account's home; no credential or global config is copied.
    """
    _absolute(binary, work, settings_path, mcp_path, plugin_root)
    if not binary.is_file() or not os.access(binary, os.X_OK) or not work.is_dir():
        raise SkillEvalError("native Claude binary or private work directory unavailable")
    identity = _native_identity()
    try:
        plugin_root.mkdir(mode=0o700)
        temporary = plugin_root / "tmp"
        temporary.mkdir(mode=0o700)
        _private_json(settings_path, _settings())
    except OSError:
        raise SkillEvalError("fresh private Claude context could not be prepared") from None
    environment = {
        **_ENVIRONMENT,
        "HOME": identity.pw_dir,
        "USER": identity.pw_name,
        "LOGNAME": identity.pw_name,
        "TMPDIR": str(temporary),
        "CLAUDE_CODE_PLUGIN_CACHE_DIR": str(plugin_root),
    }
    argv = [
        str(binary),
        "--print",
        "--restricted",
        "--tools",
        "",
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--mcp-config",
        str(mcp_path),
        "--settings",
        str(settings_path),
        "--disable-slash-commands",
        "--no-chrome",
        "--permission-mode",
        "dontAsk",
        "--permission-prompts",
        "none",
        "--no-session-persistence",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        CLAUDE_MODEL,
        "--effort",
        "high",
    ]
    return argv, environment


def check_managed_context(home: Path) -> None:
    """Fail closed on unreviewed managed sources using metadata, never contents.

    System files and drop-ins can override invocation settings. macOS reads
    ``com.anthropic.claudecode`` in device and per-user Managed Preferences;
    ordinary Preferences variants are rejected conservatively too. Claude caches
    remote policy at ``~/.claude/remote-settings.json``. This preflight does not
    prove that a new remote policy cannot arrive after it runs.
    """
    _absolute(home)
    domain = "com.anthropic.claudecode.plist"
    candidates = [
        home / ".claude" / "remote-settings.json",
        home / "Library" / "Preferences" / domain,
        _SYSTEM_PREFERENCES_ROOT / domain,
        _MANAGED_PREFERENCES_ROOT / domain,
        _MANAGED_PREFERENCES_ROOT / _native_identity().pw_name / domain,
    ]
    for root in _SYSTEM_MANAGED_ROOTS:
        candidates.extend(
            root / name
            for name in (
                "managed-settings.json",
                "managed-settings.d",
                "managed-mcp.json",
                "AGENTS.md",
            )
        )
    for candidate in candidates:
        try:
            candidate.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise SkillEvalError("managed Claude context could not be inspected") from None
        raise SkillEvalError("unreviewed managed Claude context is present")


def _validate_environment(environment: Mapping[str, str]) -> None:
    identity = _native_identity()
    expected = {
        **_ENVIRONMENT,
        "HOME": identity.pw_dir,
        "USER": identity.pw_name,
        "LOGNAME": identity.pw_name,
    }
    allowed = set(expected) | {"TMPDIR", "CLAUDE_CODE_PLUGIN_CACHE_DIR"}
    if set(environment) != allowed or any(
        environment.get(key) != value for key, value in expected.items()
    ):
        raise SkillEvalError(
            "native Claude authentication requires the sanitized launch environment"
        )
    for key in ("TMPDIR", "CLAUDE_CODE_PLUGIN_CACHE_DIR"):
        value = environment[key]
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise SkillEvalError("native Claude authentication requires private context paths")


def safe_auth_status(
    binary: Path, *, environment: Mapping[str, str], settings_path: Path, work: Path
) -> dict[str, Any]:
    """Query only native CLI auth metadata; reject keys, helpers and other routes.

    The restricted flags and same invocation settings apply before the subcommand
    to exclude user ``apiKeyHelper`` and hooks. Native stdout is parsed in memory;
    only three allowlisted facts escape. Raw stderr and account details are never
    included in errors. Auth success is not proof of model access or policy absence.
    """
    _absolute(binary, settings_path, work)
    _validate_environment(environment)
    try:
        completed = subprocess.run(
            [
                str(binary),
                "--restricted",
                "--setting-sources",
                "",
                "--settings",
                str(settings_path),
                "auth",
                "status",
                "--json",
            ],
            cwd=work,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=20,
            check=False,
        )
        if completed.returncode != 0 or len(completed.stdout) > 64 * 1024:
            raise ValueError
        status = json.loads(completed.stdout)
        if not isinstance(status, dict):
            raise ValueError
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise SkillEvalError("native Claude authentication status could not be verified") from None
    facts = {
        "authMethod": status.get("authMethod"),
        "firstParty": status.get("apiProvider") == "firstParty",
        "loggedIn": status.get("loggedIn"),
    }
    if facts != {"authMethod": "claude.ai", "firstParty": True, "loggedIn": True} or (
        type(facts["loggedIn"]) is not bool
    ):
        raise SkillEvalError("native Claude subscription authentication required")
    return facts


class InitGuard:
    """Admit the sole VM tool from observed init before the MCP gate opens.

    Any rejection permanently fails this guard and removes its owned gate. This
    detects unexpected native capabilities; it does not sandbox the host client.
    The VM MCP server must independently require the gate before each execution.
    """

    def __init__(self, gate_path: Path) -> None:
        _absolute(gate_path)
        self.gate_path = gate_path
        self.passed = False
        self._failed = False
        self._tools: set[str] = set()
        self._owns_gate = False
        try:
            gate_path.lstat()
        except FileNotFoundError:
            pass
        except OSError:
            raise ValueError("Claude init gate could not be inspected") from None
        else:
            raise ValueError("Claude init gate must be absent before observation")

    def _reject(self, message: str) -> NoReturn:
        self._failed = True
        self.passed = False
        if self._owns_gate:
            with suppress(FileNotFoundError):
                self.gate_path.unlink()
            self._owns_gate = False
        raise ValueError(message)

    def __call__(self, record: dict[str, Any]) -> None:
        if self._failed:
            self._reject("Claude init guard already failed")
        if not isinstance(record, dict) or not isinstance(record.get("type"), str):
            self._reject("Claude native event must be a typed object")
        is_init = record.get("type") == "system" and record.get("subtype") == "init"
        if not self.passed:
            if not is_init:
                self._reject("Claude first native event must be init")
            self._init(record)
            return
        if is_init:
            self._reject("Claude emitted more than one init")
        if record.get("type") == "assistant":
            message = record.get("message")
            if not isinstance(message, dict) or not isinstance(message.get("content"), list):
                self._reject("Claude assistant content is malformed")
            for block in message["content"]:
                if not isinstance(block, dict):
                    self._reject("Claude assistant content block is malformed")
                if block.get("type") == "tool_use" and (
                    not isinstance(block.get("name"), str) or block["name"] not in self._tools
                ):
                    self._reject("Claude called an unadvertised tool")
        elif record.get("type") == "stream_event":
            event = record.get("event")
            if isinstance(event, dict) and event.get("type") == "content_block_start":
                block = event.get("content_block")
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and (not isinstance(block.get("name"), str) or block["name"] not in self._tools)
                ):
                    self._reject("Claude streamed an unadvertised tool")

    def _init(self, record: dict[str, Any]) -> None:
        tools = record.get("tools")
        if record.get("apiKeySource") != "none":
            self._reject("Claude observed API key source differs from native subscription route")
        if record.get("permissionMode") != "dontAsk":
            self._reject("Claude observed permission mode differs from invocation contract")
        if record.get("model") != CLAUDE_MODEL:
            self._reject("Claude observed model differs from pinned model")
        if (
            not isinstance(tools, list)
            or any(not isinstance(tool, str) for tool in tools)
            or len(set(tools)) != len(tools)
            or not set(tools) <= _ALLOWED_TOOLS
            or VM_TOOL not in tools
        ):
            self._reject("Claude observed tool allowlist differs from VM-only contract")
        servers = record.get("mcp_servers")
        if (
            not isinstance(servers, list)
            or len(servers) != 1
            or not isinstance(servers[0], dict)
            or servers[0].get("name") != "vm"
            or servers[0].get("status") != "connected"
        ):
            self._reject("Claude must observe only the connected VM MCP server")
        agents = record.get("agents")
        if (
            not isinstance(agents, list)
            or any(not isinstance(agent, str) for agent in agents)
            or len(set(agents)) != len(agents)
            or not set(agents) <= _BUILTIN_AGENTS
        ):
            self._reject("Claude observed an unreviewed or malformed agent catalog")
        plugins = record.get("plugins")
        if not isinstance(plugins, list):
            self._reject("Claude plugin catalog is missing or malformed")
        seen_plugins: set[tuple[str, str, str]] = set()
        for plugin in plugins:
            if (
                not isinstance(plugin, dict)
                or set(plugin) != {"name", "path", "source"}
                or any(not isinstance(plugin[key], str) for key in ("name", "path", "source"))
            ):
                self._reject("Claude observed a malformed plugin identity")
            identity = (plugin["name"], plugin["path"], plugin["source"])
            if identity not in _BUILTIN_PLUGINS or identity in seen_plugins:
                self._reject("Claude observed an unreviewed or duplicate plugin identity")
            seen_plugins.add(identity)
        for key in ("skills", "slash_commands"):
            if record.get(key) != []:
                self._reject("Claude loaded unexpected or unobserved customization")
        if record.get("plugin_errors", []) != []:
            self._reject("Claude plugin loading reported errors")
        self._tools = set(tools)
        try:
            _private_json(self.gate_path, {"status": "passed"})
        except OSError:
            self._reject("Claude init gate could not be created exclusively")
        self._owns_gate = True
        self.passed = True
