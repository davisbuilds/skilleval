"""Ordinary-validation transport uses authored executables, never imported tools."""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

PATH = (
    Path(__file__).resolve().parents[1]
    / "examples/repair/completed-sessions/environment/validate.py"
)
SPEC = importlib.util.spec_from_file_location("session_environment", PATH)
assert SPEC is not None and SPEC.loader is not None
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)


def test_node_tap_keeps_nested_failure_and_skip_identities():
    text = """TAP version 13
# Subtest: ordinary suite
    # Subtest: first case
    ok 1 - first case
    # Subtest: broken case
    not ok 2 - broken case
    # Subtest: unsupported case
    ok 3 - unsupported case # SKIP platform prerequisite
not ok 1 - ordinary suite
# tests 3
# suites 1
# pass 1
# fail 1
# cancelled 0
# skipped 1
# todo 0
"""
    result = worker.parse_tap(text)
    assert result["counts"]["tests"] == 3
    assert result["failures"][0]["path"] == ["ordinary suite", "broken case"]
    assert result["skips"][0]["reason"] == "platform prerequisite"
    with pytest.raises(ValueError, match="summary"):
        worker.parse_tap(text.replace("# tests 3\n", ""))
    with pytest.raises(ValueError, match="identities"):
        worker.parse_tap(text.replace("    ok 1 - first case\n", ""))


def test_vitest_reports_exact_assertions_and_rejects_empty_suite(tmp_path):
    report = {
        "numTotalTests": 2,
        "numPassedTests": 1,
        "numFailedTests": 0,
        "numPendingTests": 1,
        "testResults": [
            {
                "name": str(tmp_path / "frontend/src/example.test.ts"),
                "assertionResults": [
                    {"fullName": "suite active", "status": "passed"},
                    {"fullName": "suite deferred", "status": "pending"},
                ],
            }
        ],
    }
    result = worker.parse_vitest(report, tmp_path)
    assert result["identities"][0]["file"] == "frontend/src/example.test.ts"
    assert result["skips"][0]["name"] == "suite deferred"
    report["numTotalTests"] = 0
    with pytest.raises(ValueError, match="zero tests"):
        worker.parse_vitest(report, tmp_path)


def test_scratch_lane_retains_logs_and_reports_real_artifacts(tmp_path, monkeypatch, capfd):
    source = tmp_path / "task"
    source.mkdir()
    (source / "package.json").write_text('{"name":"authored-fixture"}\n')
    (source / "frontend").mkdir()
    deps = tmp_path / "dependencies"
    (deps / ".bin").mkdir(parents=True)
    commands = tmp_path / "bin"
    commands.mkdir()
    pnpm = commands / "pnpm"
    pnpm.write_text(
        f"#!{sys.executable}\n"
        + """
import pathlib, sys
if sys.argv[1] == 'frontend:test':
    import json
    target = next(value.split('=', 1)[1] for value in sys.argv if value.startswith('--outputFile='))
    pathlib.Path(target).write_text(json.dumps({
        'numTotalTests': 1, 'numPassedTests': 1, 'numFailedTests': 0,
        'numPendingTests': 0, 'testResults': [{
            'name': str(pathlib.Path.cwd() / 'frontend/src/example.test.ts'),
            'assertionResults': [{'fullName': 'ordinary fixture', 'status': 'passed'}]
        }]
    }))
pathlib.Path('dist').mkdir(exist_ok=True)
pathlib.Path('dist/artifact.js').write_bytes(b'authored artifact')
print('authored stdout')
print('authored stderr', file=sys.stderr)
"""
    )
    pnpm.chmod(0o755)
    monkeypatch.setenv("PATH", str(commands) + os.pathsep + os.environ["PATH"])
    before = worker.inventory(source)
    result = worker.run_lane("frontend-tests", source, deps)
    assert result["status"] == "passed"
    assert result["existing_source_changes"] == result["captured_source_changes"] == []
    assert result["generated_artifacts"]["paths"] == ["dist/artifact.js"]
    assert result["generated_artifacts"]["total_bytes"] == 17
    assert worker.inventory(source) == before
    assert result["commands"][0]["tests"]["counts"]["numTotalTests"] == 1
    assert "ordinary-validation-" not in json.dumps(result)
    retained = capfd.readouterr().err
    assert "authored stdout" in retained and "authored stderr" in retained
