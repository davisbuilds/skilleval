"""Native context policy and real synthetic subprocesses, without model calls."""

import json
import os
import pwd
import stat
import sys
from pathlib import Path
from typing import Any

import pytest

from skilleval.claude_context import InitGuard, launch_context, safe_auth_status
from skilleval.errors import SkillEvalError


def init(**changes: object) -> dict[str, Any]:
    return {
        "type": "system",
        "subtype": "init",
        "model": "claude-opus-5-5",
        "apiKeySource": "none",
        "permissionMode": "dontAsk",
        "agents": ["Explore", "general-purpose", "Plan", "statusline-setup"],
        "tools": ["mcp__vm__execute", "EndConversation"],
        "mcp_servers": [{"name": "vm", "status": "connected"}],
        "skills": [],
        "plugins": [],
        "slash_commands": [],
        **changes,
    }


def fake_binary(tmp_path: Path, data: object, code: int = 0) -> Path:
    binary = tmp_path / "claude"
    binary.write_text(
        f"#!{sys.executable}\nimport json, sys\nprint(json.dumps({data!r}))\nsys.exit({code})\n"
    )
    binary.chmod(0o700)
    return binary


def context(tmp_path: Path, binary: Path) -> tuple[list[str], dict[str, str]]:
    work = tmp_path / "work"
    work.mkdir()
    return launch_context(
        binary, work, tmp_path / "settings.json", tmp_path / "mcp.json", tmp_path / "plugins"
    )


def test_launch_arguments_disable_native_host_tools_and_context(tmp_path: Path) -> None:
    binary = fake_binary(tmp_path, {})
    argv, environment = context(tmp_path, binary)
    assert argv[0] == str(binary)
    for flag in (
        "--restricted",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--no-chrome",
        "--no-session-persistence",
        "--verbose",
        "--print",
    ):
        assert flag in argv
    for flag, expected in (
        ("--tools", ""),
        ("--setting-sources", ""),
        ("--permission-mode", "dontAsk"),
        ("--permission-prompts", "none"),
        ("--output-format", "stream-json"),
        ("--effort", "high"),
        ("--model", "claude-opus-5-5"),
    ):
        assert argv[argv.index(flag) + 1] == expected
    assert environment["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1"
    settings = json.loads((tmp_path / "settings.json").read_text())
    assert settings["disableAllHooks"] is True
    assert settings["disableSkillShellExecution"] is True
    assert settings["autoMemoryEnabled"] is False
    assert settings["syncClaudeAiSkills"] is False
    assert settings["syncClaudeAiPlugins"] is False
    assert settings["pluginConfigs"]["cc-plugin-agents-md@builtin"]["options"] == {
        "instructionFiles": "managed-only"
    }
    assert stat.S_IMODE((tmp_path / "settings.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "plugins").stat().st_mode) == 0o700


def test_environment_drops_inherited_secrets_routes_and_process_injection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    poisoned = [
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CONFIG_DIR",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NODE_OPTIONS",
        "DYLD_INSERT_LIBRARIES",
        "CLAUDECODE",
        "CLAUDE_CODE_PLUGIN_DIRS",
        "BASH_ENV",
        "ENV",
        "PYTHONPATH",
    ]
    monkeypatch.setenv("HOME", str(tmp_path / "inherited-home"))
    for key in poisoned:
        monkeypatch.setenv(key, "synthetic-poison")
    binary = fake_binary(tmp_path, {})
    _, environment = context(tmp_path, binary)
    assert all(key not in environment for key in poisoned)
    assert "synthetic-poison" not in environment.values()
    assert environment["HOME"] == pwd.getpwuid(os.getuid()).pw_dir
    assert environment["PATH"] == "/usr/bin:/bin:/usr/sbin:/sbin"


def test_init_opens_private_gate_only_after_complete_allowlist_validation(tmp_path: Path) -> None:
    gate = tmp_path / "gate.json"
    guard = InitGuard(gate)
    assert not gate.exists()
    guard(init())
    assert json.loads(gate.read_text()) == {"status": "passed"}
    assert stat.S_IMODE(gate.stat().st_mode) == 0o600
    guard(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "name": "mcp__vm__execute", "id": "call", "input": {}}
                ]
            },
        }
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"model": "opus"},
        {"tools": ["mcp__vm__execute", "Bash"]},
        {"tools": ["EndConversation"]},
        {"tools": None},
        {"tools": ["mcp__vm__execute", {}]},
        {"mcp_servers": [{"name": "vm", "status": "failed"}]},
        {
            "mcp_servers": [
                {"name": "vm", "status": "connected"},
                {"name": "host", "status": "connected"},
            ]
        },
        {"skills": ["unexpected"]},
        {"plugins": [{"name": "unexpected"}]},
        {"skills": None},
        {"plugins": None},
        {"slash_commands": ["host-command"]},
    ],
)
def test_bad_init_never_opens_gate(tmp_path: Path, changes: dict[str, Any]) -> None:
    gate = tmp_path / "gate.json"
    guard = InitGuard(gate)
    with pytest.raises(ValueError):
        guard(init(**changes))
    assert not gate.exists()


def test_first_non_init_repeated_init_and_unadvertised_calls_revoke_gate(tmp_path: Path) -> None:
    gate = tmp_path / "gate.json"
    guard = InitGuard(gate)
    with pytest.raises(ValueError):
        guard({"type": "assistant", "message": {"content": []}})
    assert not gate.exists()
    guard = InitGuard(gate)
    guard(init())
    with pytest.raises(ValueError):
        guard(init())
    assert not gate.exists()
    guard = InitGuard(gate)
    guard(init())
    with pytest.raises(ValueError):
        guard(
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {}}]},
            }
        )
    assert not gate.exists()


def test_existing_or_symlink_gate_is_never_overwritten(tmp_path: Path) -> None:
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("preserved")
    gate = tmp_path / "gate.json"
    gate.symlink_to(sentinel)
    with pytest.raises((ValueError, SkillEvalError)):
        InitGuard(gate)(init())
    assert sentinel.read_text() == "preserved"


def test_auth_status_filters_native_json_and_uses_same_context_flags(tmp_path: Path) -> None:
    status = {
        "loggedIn": True,
        "authMethod": "claude.ai",
        "apiProvider": "firstParty",
        "email": "synthetic-private",
        "accessToken": "synthetic-secret",
    }
    binary = fake_binary(tmp_path, status)
    _, environment = context(tmp_path, binary)
    result = safe_auth_status(
        binary,
        environment=environment,
        settings_path=tmp_path / "settings.json",
        work=tmp_path / "work",
    )
    assert result == {"loggedIn": True, "authMethod": "claude.ai", "firstParty": True}
    assert "synthetic" not in repr(result)


@pytest.mark.parametrize(
    "status",
    [
        {"loggedIn": False, "authMethod": "none", "apiProvider": "firstParty"},
        {"loggedIn": True, "authMethod": "api_key", "apiProvider": "firstParty"},
        {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "bedrock"},
        {"loggedIn": 1, "authMethod": "claude.ai", "apiProvider": "firstParty"},
        [],
    ],
)
def test_auth_status_refuses_other_routes_with_value_free_errors(
    tmp_path: Path, status: object
) -> None:
    binary = fake_binary(tmp_path, status)
    _, environment = context(tmp_path, binary)
    with pytest.raises(SkillEvalError) as caught:
        safe_auth_status(
            binary,
            environment=environment,
            settings_path=tmp_path / "settings.json",
            work=tmp_path / "work",
        )
    assert "synthetic-secret" not in str(caught.value)


def managed_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    import skilleval.claude_context as policy

    system = tmp_path / "system"
    mdm = tmp_path / "managed-preferences"
    preferences = tmp_path / "preferences"
    for root in (system, mdm, preferences):
        root.mkdir()
    monkeypatch.setattr(policy, "_SYSTEM_MANAGED_ROOTS", (system,))
    monkeypatch.setattr(policy, "_MANAGED_PREFERENCES_ROOT", mdm)
    monkeypatch.setattr(policy, "_SYSTEM_PREFERENCES_ROOT", preferences)
    return system, mdm, preferences


@pytest.mark.parametrize(
    "location",
    [
        "managed-settings.json",
        "managed-settings.d",
        "managed-mcp.json",
        "AGENTS.md",
        "remote",
        "mdm-device",
        "mdm-user",
        "preferences",
    ],
)
def test_managed_context_presence_detector_has_real_positive_controls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    location: str,
) -> None:
    from skilleval.claude_context import check_managed_context

    system, mdm, preferences = managed_roots(tmp_path, monkeypatch)
    home = tmp_path / "home"
    home.mkdir()
    check_managed_context(home)
    domain = "com.anthropic.claudecode.plist"
    if location == "remote":
        target = home / ".claude" / "remote-settings.json"
    elif location == "mdm-device":
        target = mdm / domain
    elif location == "mdm-user":
        target = mdm / pwd.getpwuid(os.getuid()).pw_name / domain
    elif location == "preferences":
        target = preferences / domain
    else:
        target = system / location
    target.parent.mkdir(parents=True, exist_ok=True)
    if location == "managed-settings.d":
        target.mkdir()
    else:
        target.write_text("synthetic-policy-secret: deliberately not JSON")
    with pytest.raises(SkillEvalError) as caught:
        check_managed_context(home)
    assert "synthetic-policy-secret" not in str(caught.value)
    target.rmdir() if target.is_dir() else target.unlink()
    check_managed_context(home)


def test_dangling_managed_symlink_still_requires_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from skilleval.claude_context import check_managed_context

    system, _, _ = managed_roots(tmp_path, monkeypatch)
    home = tmp_path / "home"
    home.mkdir()
    (system / "managed-settings.json").symlink_to(tmp_path / "absent-policy")
    with pytest.raises(SkillEvalError):
        check_managed_context(home)


def test_existing_plugin_root_and_settings_are_not_reused_or_overwritten(tmp_path: Path) -> None:
    binary = fake_binary(tmp_path, {})
    work = tmp_path / "work"
    work.mkdir()
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    sentinel = plugins / "keep"
    sentinel.write_text("owned elsewhere")
    with pytest.raises(SkillEvalError):
        launch_context(binary, work, tmp_path / "settings.json", tmp_path / "mcp.json", plugins)
    assert sentinel.read_text() == "owned elsewhere"
    settings = tmp_path / "existing-settings.json"
    settings.write_text("preserved")
    with pytest.raises(SkillEvalError):
        launch_context(binary, work, settings, tmp_path / "mcp.json", tmp_path / "fresh-plugins")
    assert settings.read_text() == "preserved"


def test_synthetic_auth_process_observes_restricted_argv_and_clean_environment(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "claude"
    proof = tmp_path / "proof.json"
    binary.write_text(
        f"#!{sys.executable}\nimport json, os, sys\n"
        f"from pathlib import Path\nPath({str(proof)!r}).write_text(json.dumps({{"
        "'argv': sys.argv[1:], 'environment_keys': sorted(os.environ)}))\n"
        "print(json.dumps({'loggedIn':True,'authMethod':'claude.ai','apiProvider':'firstParty',"
        "'email':'synthetic-private','token':'synthetic-secret'}))\n"
    )
    binary.chmod(0o700)
    _, environment = context(tmp_path, binary)
    status = safe_auth_status(
        binary,
        environment=environment,
        settings_path=tmp_path / "settings.json",
        work=tmp_path / "work",
    )
    observed = json.loads(proof.read_text())
    assert observed["argv"] == [
        "--restricted",
        "--setting-sources",
        "",
        "--settings",
        str(tmp_path / "settings.json"),
        "auth",
        "status",
        "--json",
    ]
    assert "ANTHROPIC_API_KEY" not in observed["environment_keys"]
    assert "synthetic" not in repr(status)


def test_auth_probe_rejects_environment_injection_before_launch(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    binary = tmp_path / "claude"
    binary.write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\n"
    )
    binary.chmod(0o700)
    _, environment = context(tmp_path, binary)
    environment["ANTHROPIC_API_KEY"] = "synthetic-secret"
    with pytest.raises(SkillEvalError):
        safe_auth_status(
            binary,
            environment=environment,
            settings_path=tmp_path / "settings.json",
            work=tmp_path / "work",
        )
    assert not marker.exists()


def test_auth_nonzero_and_malformed_output_never_leak_subprocess_diagnostics(
    tmp_path: Path,
) -> None:
    binary = tmp_path / "claude"
    binary.write_text(
        f"#!{sys.executable}\nimport sys\n"
        "print('synthetic-secret-invalid-json')\n"
        "print('synthetic-secret-stderr', file=sys.stderr)\n"
    )
    binary.chmod(0o700)
    _, environment = context(tmp_path, binary)
    with pytest.raises(SkillEvalError) as caught:
        safe_auth_status(
            binary,
            environment=environment,
            settings_path=tmp_path / "settings.json",
            work=tmp_path / "work",
        )
    assert "synthetic-secret" not in str(caught.value)


def test_auth_nonzero_exit_cannot_be_admitted_by_valid_status_json(tmp_path: Path) -> None:
    binary = fake_binary(
        tmp_path,
        {
            "loggedIn": True,
            "authMethod": "claude.ai",
            "apiProvider": "firstParty",
        },
        code=1,
    )
    _, environment = context(tmp_path, binary)
    with pytest.raises(SkillEvalError):
        safe_auth_status(
            binary,
            environment=environment,
            settings_path=tmp_path / "settings.json",
            work=tmp_path / "work",
        )


def native_init_fixture(**changes: object) -> dict[str, Any]:
    """Minimized public-shaped init observed on native Claude Code 2.1.321."""
    return (
        init(
            claude_code_version="2.1.321",
            tools=["mcp__vm__execute"],
            mcp_servers=[{"name": "vm", "status": "connected", "source": "dynamic"}],
            additional_directories=[],
            plugins=[
                {
                    "name": "cc-plugin-agents-md",
                    "path": "builtin",
                    "source": "cc-plugin-agents-md@builtin",
                },
                {
                    "name": "cc-plugin-privacy",
                    "path": "builtin",
                    "source": "cc-plugin-privacy@builtin",
                },
                {
                    "name": "cc-plugin-plugin-authoring",
                    "path": "builtin",
                    "source": "cc-plugin-plugin-authoring@builtin",
                },
            ],
        )
        | changes
    )


def test_observed_native_builtin_components_open_gate_without_foreign_customizations(
    tmp_path: Path,
) -> None:
    gate = tmp_path / "gate.json"
    guard = InitGuard(gate)
    guard(native_init_fixture())
    assert guard.passed is True
    assert json.loads(gate.read_text()) == {"status": "passed"}
    assert stat.S_IMODE(gate.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "plugins",
    [
        [],
        [{"name": "cc-plugin-privacy", "path": "builtin", "source": "cc-plugin-privacy@builtin"}],
    ],
)
def test_known_builtin_subset_or_empty_plugin_catalog_remains_admissible(
    tmp_path: Path,
    plugins: list[dict[str, Any]],
) -> None:
    guard = InitGuard(tmp_path / "gate.json")
    guard(native_init_fixture(plugins=plugins))
    assert guard.passed is True


@pytest.mark.parametrize(
    "plugin",
    [
        {
            "name": "cc-plugin-agents-md",
            "path": "/host/plugin",
            "source": "cc-plugin-agents-md@builtin",
        },
        {"name": "cc-plugin-agents-md", "path": "builtin", "source": "cc-plugin-agents-md@foreign"},
        {"name": "custom-plugin", "path": "builtin", "source": "cc-plugin-agents-md@builtin"},
        {"name": "custom-plugin", "path": "builtin", "source": "custom-plugin@builtin"},
        {
            "name": "cc-plugin-privacy",
            "path": "builtin",
            "source": "cc-plugin-privacy@builtin",
            "unexpected": "unreviewed",
        },
        {"name": "cc-plugin-privacy", "path": "builtin"},
        {"name": [], "path": "builtin", "source": "cc-plugin-privacy@builtin"},
        "cc-plugin-privacy@builtin",
    ],
)
def test_builtin_plugin_identity_requires_exact_native_name_path_and_source(
    tmp_path: Path,
    plugin: object,
) -> None:
    gate = tmp_path / "gate.json"
    with pytest.raises(ValueError):
        InitGuard(gate)(native_init_fixture(plugins=[plugin]))
    assert not gate.exists()


def test_duplicate_vendor_builtin_plugin_is_not_a_clean_catalog(tmp_path: Path) -> None:
    plugins = native_init_fixture()["plugins"]
    gate = tmp_path / "gate.json"
    with pytest.raises(ValueError):
        InitGuard(gate)(native_init_fixture(plugins=[*plugins, plugins[0]]))
    assert not gate.exists()


@pytest.mark.parametrize(
    "agents",
    [
        ["custom-agent"],
        ["Explore", "custom-agent"],
        ["Explore", "Explore"],
        None,
        [[], "Plan"],
    ],
)
def test_custom_duplicate_or_unobserved_agent_catalog_is_rejected(
    tmp_path: Path,
    agents: object,
) -> None:
    gate = tmp_path / "gate.json"
    with pytest.raises(ValueError):
        InitGuard(gate)(init(agents=agents))
    assert not gate.exists()


@pytest.mark.parametrize(
    "agents", [[], ["Explore"], ["general-purpose", "Plan", "statusline-setup"]]
)
def test_builtin_agent_subset_is_catalog_metadata_without_added_tools(
    tmp_path: Path,
    agents: list[str],
) -> None:
    guard = InitGuard(tmp_path / "gate.json")
    guard(native_init_fixture(agents=agents))
    assert guard.passed is True


@pytest.mark.parametrize(
    "changes",
    [
        {"apiKeySource": "ANTHROPIC_API_KEY"},
        {"apiKeySource": "apiKeyHelper"},
        {"apiKeySource": None},
        {"permissionMode": "bypassPermissions"},
        {"permissionMode": "default"},
        {"permissionMode": None},
    ],
)
def test_observed_auth_source_and_permission_mode_must_match_subscription_route(
    tmp_path: Path,
    changes: dict[str, Any],
) -> None:
    gate = tmp_path / "gate.json"
    with pytest.raises(ValueError):
        InitGuard(gate)(init(**changes))
    assert not gate.exists()


def test_known_vendor_plugins_do_not_admit_loaded_skill_content(tmp_path: Path) -> None:
    gate = tmp_path / "gate.json"
    with pytest.raises(ValueError):
        InitGuard(gate)(native_init_fixture(skills=["plugin-authoring"]))
    assert not gate.exists()
