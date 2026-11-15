"""Environment transport tests execute authored fixtures, never captured code."""

import base64
import copy
import hashlib
import importlib.util
import json
import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

ENVIRONMENT = (
    Path(__file__).resolve().parents[1] / "examples/document/operational-metrics/environment"
)
SPEC = importlib.util.spec_from_file_location(
    "operational_metrics_environment", ENVIRONMENT / "validate.py"
)
assert SPEC is not None and SPEC.loader is not None
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


def test_application_state_and_provider_configuration_are_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", "/authored-host-home")
    monkeypatch.setenv("CODEX_HOME", "/authored-host-codex")
    monkeypatch.setenv("OPENAI_API_KEY", "authored-secret-placeholder")
    monkeypatch.setenv("AGENTMONITOR_DB_PATH", "/authored-host-database")
    monkeypatch.setenv("AGENTMONITOR_INSIGHTS_PROVIDER", "authored-provider")
    monkeypatch.setenv("NODE_OPTIONS", "--import=/authored-host-module")
    environment = worker.application_environment(tmp_path)
    assert environment["HOME"] == str(tmp_path / "home")
    assert environment["CODEX_HOME"] == str(tmp_path / "codex-home")
    assert environment["AGENTMONITOR_DB_PATH"] == str(tmp_path / "application.db")
    assert environment["AGENTMONITOR_GITHUB_LOOKUPS"] == "false"
    assert "OPENAI_API_KEY" not in environment
    assert "AGENTMONITOR_INSIGHTS_PROVIDER" not in environment
    assert "NODE_OPTIONS" not in environment
    assert not any((tmp_path / "codex-home").iterdir())


def authored_workspace(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "frontend").mkdir()
    (source / "package.json").write_text('{"name":"authored-fixture"}\n')
    dependencies = tmp_path / "dependencies"
    (dependencies / ".bin").mkdir(parents=True)
    commands = tmp_path / "bin"
    commands.mkdir()
    executable = commands / "pnpm"
    executable.write_text(
        f"#!{sys.executable}\n"
        + """
import json, os, pathlib, sys
project = pathlib.Path.cwd()
assert not pathlib.Path(os.environ['HOME']).is_relative_to(project)
assert not pathlib.Path(os.environ['CODEX_HOME']).is_relative_to(project)
assert not pathlib.Path(os.environ['AGENTMONITOR_DB_PATH']).is_relative_to(project)
assert 'OPENAI_API_KEY' not in os.environ
assert 'NODE_OPTIONS' not in os.environ
report = next(value.split('=',1)[1] for value in sys.argv if value.startswith('--outputFile='))
pathlib.Path(report).write_text(json.dumps({
  'numTotalTests': 1, 'numPassedTests': 1, 'numFailedTests': 0, 'numPendingTests': 0,
  'testResults': [{'name':str(project/'frontend/src/example.test.ts'),
    'assertionResults':[{'fullName':'authored positive control','status':'passed'}]}]
}))
(project/'dist').mkdir()
(project/'dist/artifact.js').write_bytes(b'authored fixture output')
print('authored stdout')
print('authored stderr',file=sys.stderr)
"""
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(commands) + os.pathsep + os.environ["PATH"])
    return source, dependencies, executable


def test_lane_uses_scratch_keeps_source_and_retains_observations(tmp_path, monkeypatch, capfd):
    source, dependencies, _ = authored_workspace(tmp_path, monkeypatch)
    before = worker.inventory(source)
    result = worker.run_lane("frontend-tests", source, dependencies)
    assert result["status"] == "passed"
    assert result["commands"][0]["tests"]["counts"]["numTotalTests"] == 1
    assert result["application_home"] == {
        "outside_source": True,
        "database_outside_source": True,
        "codex_entries": [],
    }
    assert result["generated_artifacts"]["paths"] == ["dist/artifact.js"]
    assert result["generated_artifacts"]["total_bytes"] == 23
    assert result["existing_source_changes"] == result["captured_source_changes"] == []
    assert worker.inventory(source) == before
    retained = capfd.readouterr().err
    assert "authored stdout" in retained and "authored stderr" in retained


@pytest.mark.parametrize(
    "mutation", ["failure", "truncation", "empty_report", "source_change", "budget"]
)
def test_lane_rejects_false_success(tmp_path, monkeypatch, mutation):
    source, dependencies, executable = authored_workspace(tmp_path, monkeypatch)
    script = executable.read_text()
    if mutation == "failure":
        script += "\nsys.exit(3)\n"
    elif mutation == "truncation":
        monkeypatch.setattr(worker, "RAW_LIMIT", 100)
    elif mutation == "empty_report":
        script = script.replace("'numTotalTests': 1", "'numTotalTests': 0")
    elif mutation == "source_change":
        script += "\n(project/'package.json').write_text('changed')\n"
    elif mutation == "budget":
        monkeypatch.setattr(worker, "MAX_TOTAL_BYTES", 1)
    executable.write_text(script)
    executable.chmod(0o755)
    result = worker.run_lane("frontend-tests", source, dependencies)
    assert result["status"] == "failed"
    assert worker.inventory(source)["package.json"]["size"] == 28
    if mutation == "failure":
        assert result["commands"][0]["exit_code"] == 3
    elif mutation == "truncation":
        assert result["commands"][0]["output_complete"] is False
    elif mutation == "empty_report":
        assert "zero tests" in result["commands"][0]["parse_error"]
    elif mutation == "source_change":
        assert result["existing_source_changes"] == ["package.json"]
    else:
        assert result["collection_budget"]["total_within_limit"] is False


def test_entry_point_refuses_the_host_before_running_tools(tmp_path):
    result = subprocess.run(
        [sys.executable, str(ENVIRONMENT / "validate.py"), "checks"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "requires the explicit solver-isolated-v1 guest route" in result.stderr


def test_fixture_has_positive_empty_and_accounting_controls():
    fixture = json.loads((ENVIRONMENT / "metrics-fixture.json").read_bytes())
    rows = fixture["rows"]
    positive = [row for row in rows if row["attrs"] == {"state": "succeeded"}]
    assert len(positive) == 2 and sum(row["value"] for row in positive) == 5
    assert any(row["attrs"] == {"state": "skipped_rate_limit"} for row in rows)
    assert any(row["attrs"] is None and row["agent_type"] == "codex" for row in rows)
    assert any(row["client_timestamp"] < fixture["since"] for row in rows)
    assert any(row["agent_type"] == "claude" for row in rows)


def test_node_launcher_defaults_survive_minimal_environment_and_preserve_overrides(tmp_path):
    launcher = ENVIRONMENT.parents[3] / "environments/operational-metrics/node-launcher.sh"
    binary = tmp_path / "authored-node"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "print(json.dumps({'environment':dict(os.environ),'argv':sys.argv[1:]}))\n"
    )
    binary.chmod(0o755)
    adapted = tmp_path / "node-launcher.sh"
    adapted.write_text(
        launcher.read_text().replace("/usr/local/lib/node-runtime/bin/node", str(binary))
    )
    adapted.chmod(0o755)
    for overrides in (
        {},
        {
            "PLAYWRIGHT_CHROME_EXECUTABLE_PATH": "/explicit/chrome",
            "FONTCONFIG_FILE": "explicit.conf",
        },
    ):
        result = subprocess.run(
            [str(adapted), "--authored", "two words"],
            env={"PATH": os.environ["PATH"], **overrides},
            capture_output=True,
            text=True,
            check=True,
        )
        observed = json.loads(result.stdout)
        environment = observed["environment"]
        assert observed["argv"] == ["--authored", "two words"]
        assert environment["PLAYWRIGHT_CHROME_EXECUTABLE_PATH"] == overrides.get(
            "PLAYWRIGHT_CHROME_EXECUTABLE_PATH",
            "/usr/local/share/chrome-for-testing/chrome-linux-arm64/chrome",
        )
        assert environment["FONTCONFIG_FILE"] == overrides.get("FONTCONFIG_FILE", "fonts.conf")
        assert environment["FONTCONFIG_PATH"] == "/usr/local/share/chrome-for-testing/fontconfig"
        assert environment["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] == "1"


def test_summary_omits_dynamic_artifacts_and_keeps_failures_and_controls(capfd):
    def chunk(kind, data):
        return len(data).to_bytes(4) + kind + data + zlib.crc32(kind + data).to_bytes(4)

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
        + chunk(b"IEND", b"")
    )
    assert worker.valid_png(png)
    assert not worker.valid_png(png[:-1])
    browser = {
        "browser": "156.0.8078.80",
        "uid": 61000,
        "database_is_synthetic": True,
        "schema": "frozen built initSchema",
        "fixture_rows": 7,
        "metrics": [],
        "empty_control": True,
        "browser_same_origin_metrics": True,
        "navigation": ["Monitor", "Analytics", "Monitor"],
        "page_errors": [],
        "blocked_origins": [],
        "screenshot": {
            "base64": base64.b64encode(png).decode(),
            "bytes": len(png),
            "sha256": hashlib.sha256(png).hexdigest(),
        },
    }
    browser = worker.browser_observation(json.dumps(browser))
    observed = {
        "lane": "browser",
        "status": "failed",
        "existing_source_changes": [],
        "captured_source_changes": [],
        "application_home": {
            "outside_source": True,
            "database_outside_source": True,
            "codex_entries": ["dynamic"],
        },
        "commands": [
            {
                "argv": ["node", "{browser}"],
                "exit_code": 1,
                "timed_out": False,
                "output_complete": True,
                "browser": browser,
                "tests": {
                    "counts": {"fail": 1, "skipped": 1},
                    "failures": ["failure identity"],
                    "skips": ["skip identity"],
                },
            }
        ],
        "generated_artifacts": {"total_bytes": 42},
        "collection_budget": {
            "files": 4,
            "total_bytes": 42,
            "max_bytes": 30,
            "files_within_limit": True,
            "total_within_limit": True,
            "largest_within_limit": True,
        },
    }
    alternate = copy.deepcopy(observed)
    alternate["commands"][0]["browser"]["screenshot"]["base64"] = "other dynamic screenshot"
    alternate["application_home"]["codex_entries"] = ["another support filename"]
    alternate["generated_artifacts"]["total_bytes"] = 73
    alternate["collection_budget"]["total_bytes"] = 73
    summary = worker.stable_summary(observed)
    assert summary == worker.stable_summary(alternate)
    assert summary["commands"][0]["tests"]["failures"] == ["failure identity"]
    assert summary["commands"][0]["tests"]["skips"] == ["skip identity"]
    assert summary["commands"][0]["browser"]["screenshot_valid_png"] is True
    assert "base64" not in json.dumps(summary)
    assert worker.retain_observation(observed) is True
    retained = json.loads(capfd.readouterr().err.split("FULL OBSERVATION\n", 1)[1])
    screenshot = retained["commands"][0]["browser"]["screenshot"]
    assert base64.b64decode(screenshot["base64"]) == png
    assert hashlib.sha256(png).hexdigest() == screenshot["sha256"]
    browser["screenshot"]["sha256"] = "incorrect"
    with pytest.raises(ValueError, match="screenshot"):
        worker.browser_observation(json.dumps(browser))
