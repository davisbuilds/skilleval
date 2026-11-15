"""Authored observer transport/controls only; imported app runs exclusively in VM."""

import base64
import hashlib
import importlib.util
import io
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "examples/document/operational-metrics"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


worker = load("downstream_observer", ROOT / "downstream/validate.py")
controls = load("downstream_controls", ROOT / "downstream/controls.py")
environment = load("downstream_environment", ROOT / "environment/validate.py")


def test_export_preserves_raw_evidence_not_just_a_path_or_summary(tmp_path):
    (tmp_path / "browser").mkdir()
    raw = b'{"semantic_verdict":"unassessed","requests":[{"occurrences":2,"total_value":9}]}'
    (tmp_path / "browser/observation.json").write_bytes(raw)
    (tmp_path / "browser/view.aria.txt").write_text('table "Reports"\nrow "2 9"\n')
    (tmp_path / "browser/view.png").write_bytes(b"authored-image-fixture")
    (tmp_path / "browser/real.trace.zip").write_bytes(b"explicit diagnostic omission")
    result = {"execution_status": "observed", "semantic_verdict": "unassessed"}
    encoded = worker.export_envelope(tmp_path, result)
    assert encoded["kind"] == "operational-metrics-downstream-envelope"
    zipped = base64.b64decode(encoded["base64"], validate=True)
    assert len(zipped) == encoded["bytes"]
    assert hashlib.sha256(zipped).hexdigest() == encoded["sha256"]
    with zipfile.ZipFile(io.BytesIO(zipped)) as archive:
        assert archive.read("browser/observation.json") == raw
        assert archive.read("browser/view.aria.txt").startswith(b"table")
        assert archive.read("browser/view.png") == b"authored-image-fixture"
        assert "browser/real.trace.zip" not in archive.namelist()
    assert encoded["summary"]["semantic_verdict"] == "unassessed"
    assert encoded["omitted_diagnostics"][0]["path"] == "browser/real.trace.zip"
    assert {row["path"] for row in encoded["files"]} == {
        "browser/observation.json",
        "browser/view.aria.txt",
        "browser/view.png",
    }


def test_export_refuses_incomplete_retention_or_unsafe_members(tmp_path, monkeypatch):
    (tmp_path / "known-positive.txt").write_text("retained evidence")
    assert worker.export_envelope(tmp_path, {})["files"]
    monkeypatch.setattr(worker, "MAX_ZIP_BYTES", 1)
    with pytest.raises(ValueError, match="budget"):
        worker.export_envelope(tmp_path, {})
    monkeypatch.setattr(worker, "MAX_ZIP_BYTES", 650 * 1024)
    (tmp_path / "escape").symlink_to(tmp_path / "known-positive.txt")
    with pytest.raises(ValueError, match="symlink"):
        worker.export_envelope(tmp_path, {})
    (tmp_path / "escape").unlink()
    names = set()
    worker.archive_name("known-positive.txt", names)
    with pytest.raises(ValueError, match="alias"):
        worker.archive_name("KNOWN-positive.txt", names)
    for invalid in ("/absolute", "../escape", "a/../escape", "a\\b", "a//b"):
        with pytest.raises(ValueError, match="unsafe"):
            worker.archive_name(invalid, set())


def test_source_scope_reports_added_deleted_backend_dependencies_and_patch(tmp_path):
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    for root in (baseline, candidate):
        (root / "frontend").mkdir(parents=True)
        (root / "src").mkdir()
        (root / "frontend/view.svelte").write_text("<p>old</p>\n")
        (root / "deleted.txt").write_text("remove\n")
    (candidate / "frontend/view.svelte").write_text("<p>new</p>\n")
    (candidate / "src/store.ts").write_text("backend mutation\n")
    (candidate / "pnpm-lock.yaml").write_text("dependency mutation\n")
    (candidate / "deleted.txt").unlink()
    observed, patch = worker.source_observation(baseline, candidate, environment.inventory)
    assert {row["path"] for row in observed["changes"]} == {
        "frontend/view.svelte",
        "src/store.ts",
        "pnpm-lock.yaml",
        "deleted.txt",
    }
    assert observed["outside_frontend_or_docs"] == ["deleted.txt", "pnpm-lock.yaml", "src/store.ts"]
    assert observed["dependency_changes"] == ["pnpm-lock.yaml"]
    assert "+<p>new</p>" in patch and "-<p>old</p>" in patch
    assert observed["scope_verdict"] == "review-required"


@pytest.mark.parametrize("design", ["monitor-table", "analytics-cards"])
def test_authored_controls_are_actual_integrations_with_distinguishing_mutations(tmp_path, design):
    def app(root):
        source = root / "frontend/src/App.svelte"
        source.parent.mkdir(parents=True)
        source.write_text('<script lang="ts">\n</script>\n<MonitorPage />\n<AnalyticsShell />\n')
        return source

    positive = tmp_path / "positive"
    positive_app = app(positive)
    driver = controls.apply(positive, design, "none")
    assert "<OperatorMemoryControl />" in positive_app.read_text()
    assert driver["enter"][0]["target"]["name"] == (
        "Monitor" if design == "monitor-table" else "Analytics"
    )
    assert driver["range"][0]["action"] == "select"
    assert driver["range_return"][0]["value"] == "24"
    assert driver["range_windows"] == {"wide_hours": 168, "narrow_hours": 24}
    original = controls.control_bytes(design, "none")
    swapped = controls.control_bytes(design, "swapped-measures")
    assert original != swapped
    assert b"{row.occurrences}" in original and b"{row.total_value}" in original
    assert b"Memory is healthy; no rate limit skips." in controls.control_bytes(
        design, "false-health"
    )
    unused = tmp_path / "unused"
    unused_app = app(unused)
    prior = unused_app.read_bytes()
    controls.apply(unused, design, "unwired")
    assert unused_app.read_bytes() == prior
    assert (unused / "frontend/src/OperatorMemoryControl.svelte").read_bytes() == original
    # This host test proves authored patch structure, not browser rejection.


def test_entry_point_refuses_host_before_importing_candidate(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "downstream/validate.py"),
            "--baseline",
            str(tmp_path),
            "--output",
            str(tmp_path / "out"),
            "--assets",
            str(tmp_path),
            "--environment-helper",
            str(tmp_path / "must-not-import.py"),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode != 0
    assert "requires the explicit solver-isolated-v1 guest route" in completed.stderr
    assert not (tmp_path / "out").exists()


def test_inventory_only_source_scope_records_boundary_and_baseline_hashes(tmp_path):
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "view.txt").write_text("changed content")
    baseline = tmp_path / "baseline-inventory.json"
    baseline.write_text(json.dumps({"view.txt": {"sha256": "old", "size": 2, "executable": False}}))
    observed, patch = worker.source_observation(baseline, candidate, environment.inventory)
    assert observed["baseline_bytes_available"] is False
    assert observed["changes"][0]["before"]["sha256"] == "old"
    assert "parent repair artifact" in observed["source_diff_authority"]
    assert patch == ""


def test_lane_packaging_freezes_trusted_assets_and_verifies_baseline_without_execution(tmp_path):
    from skilleval import evidence

    prepare = load("downstream_prepare", ROOT / "downstream/prepare.py")
    source = tmp_path / "source"
    source.mkdir()
    (source / "danger.py").write_text("raise RuntimeError('must not execute captured code')\n")
    (source / ".git").mkdir()
    (source / ".git/HEAD").write_text("ref: refs/heads/main\n")
    baseline = tmp_path / "baseline"
    receipt = evidence.snapshot(source, baseline)
    driver = tmp_path / "driver.json"
    driver.write_text(json.dumps(controls.driver("monitor-table")))
    argv = prepare.lane_argv(driver, baseline, receipt["manifest_sha256"])
    assert argv[:3] == ("python3", "-I", "-c")
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(argv[-1]))) as archive:
        inventory = json.loads(archive.read("baseline-inventory.json"))
        assert list(inventory) == ["danger.py"]
        assert (
            inventory["danger.py"]["sha256"]
            == hashlib.sha256((source / "danger.py").read_bytes()).hexdigest()
        )
        assert archive.read("original-request.txt") == (ROOT / "prompt.txt").read_bytes()
        assert archive.read("observe.mjs") == (ROOT / "downstream/observe.mjs").read_bytes()
        assert archive.read("scenarios.mjs") == (ROOT / "downstream/scenarios.mjs").read_bytes()
        packed_driver = json.loads(archive.read("driver.json"))
        assert packed_driver["retry"]
        assert packed_driver["range_return"][0]["value"] == "24"
        assert packed_driver["range_windows"] == {"wide_hours": 168, "narrow_hours": 24}
        assert packed_driver["keyboard"][1] == {"action": "press", "key": "Enter"}
        neutral = json.loads(archive.read("contract.json"))
        assert set(neutral) == {"api_fixture"}
        assert "qualification_controls" not in archive.read("contract.json").decode()
        assert "mutation" not in archive.read("driver.json").decode()
        assert "design" not in archive.read("driver.json").decode()
        assert json.loads(archive.read("driver.json"))["enter"][0]["target"]["name"] == "Monitor"
    # The same argv refuses host execution before loading trusted/candidate code.
    completed = subprocess.run(argv, cwd=tmp_path, text=True, capture_output=True, check=False)
    assert completed.returncode != 0
    assert "requires the explicit solver-isolated-v1 guest route" in completed.stderr
    (baseline / "danger.py").chmod(0o600)
    (baseline / "danger.py").write_text("tampered\n")
    with pytest.raises(Exception, match=r"digest|match|inventory"):
        prepare.lane_argv(driver, baseline, receipt["manifest_sha256"])


def test_runner_uses_scratch_and_retains_raw_command_and_browser_evidence(tmp_path, monkeypatch):
    source = tmp_path / "source"
    (source / "frontend").mkdir(parents=True)
    (source / "package.json").write_text('{"name":"authored-only"}\n')
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(environment.inventory(source)))
    dependencies = tmp_path / "deps"
    (dependencies / ".bin").mkdir(parents=True)
    tools = tmp_path / "tools"
    tools.mkdir()
    pnpm = tools / "pnpm"
    pnpm.write_text(f"#!{sys.executable}\nprint('authored build stdout')\n")
    node = tools / "node"
    node.write_text(
        f"#!{sys.executable}\n"
        + """
import json, os, pathlib, sys
project = pathlib.Path.cwd()
assert not pathlib.Path(os.environ['HOME']).is_relative_to(project)
assert not pathlib.Path(os.environ['AGENTMONITOR_DB_PATH']).is_relative_to(project)
assert 'OPENAI_API_KEY' not in os.environ
output = pathlib.Path(sys.argv[5])
output.mkdir()
(output/'observation.json').write_text(json.dumps({'execution_complete': True,
  'semantic_verdict':'unassessed','store':{'unchanged':True},
  'api':{'regression_unchanged':True}}))
(output/'view.aria.txt').write_text('authored consumer evidence')
print('authored browser stdout')
print('authored browser stderr',file=sys.stderr)
"""
    )
    for executable in (pnpm, node):
        executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tools) + ":" + __import__("os").environ["PATH"])
    monkeypatch.setenv("OPENAI_API_KEY", "authored-placeholder")
    assets = dict.fromkeys(
        ("observe.mjs", "contract.json", "driver.json", "fixture.json", "original-request.txt"),
        b"authored fixture",
    )
    output = tmp_path / "retained"
    before = environment.inventory(source)
    result = worker.run(source, baseline, dependencies, output, assets, environment)
    assert result["execution_status"] == "observed"
    assert result["semantic_verdict"] == "unassessed"
    assert result["browser_execution_complete"] is True
    assert (output / "command-0/stdout").read_text().strip() == "authored build stdout"
    assert (output / "command-1/stderr").read_text().strip() == "authored browser stderr"
    assert (output / "browser/view.aria.txt").read_text() == "authored consumer evidence"
    assert environment.inventory(source) == before
    envelope = worker.export_envelope(output, result)
    assert envelope["summary"]["candidate_source_changes"] == []


def test_authored_fault_protocol_reaches_recovery_refresh_and_supported_api_scope():
    """Exercise pure authored logic; no AgentMonitor imports or browser run here."""
    script = """
import { regressionRoutes, fixtureResponse, retainScreenshot }
  from './examples/document/operational-metrics/downstream/scenarios.mjs';
const rows = [{metric_name:'codex.memory.startup',attrs:{state:'succeeded'},
  occurrences:2,total_value:9,last_seen:'2026-11-07 12:00:00'}];
const url = 'http://localhost/api/v2/metrics?limit=200';
const response = (mode,phase) => fixtureResponse(rows,mode,url,phase);
console.log(JSON.stringify({routes:regressionRoutes,
  screenshots:['fixture','empty','narrow-keyboard','real','loading','race'].flatMap(mode =>
    ['initial','failure','refreshed','pending'].filter(label => retainScreenshot(mode,label))
      .map(label => mode+'/'+label)),
  initialFailure:response('initial-error','initial'),
  initialRecovery:response('initial-error','initial-recovered'),
  initial:response('fixture','initial'), refreshed:response('fixture','refreshed'),
  priorGood:response('refresh-error-recovery','initial'),
  refreshFailed:response('refresh-error-recovery','refresh-failed'),
  refreshRecovered:response('refresh-error-recovery','recovered'),
  bounded:fixtureResponse(rows,'bounded',url),
  maximum:fixtureResponse(rows,'bounded','http://localhost/api/v2/metrics?limit=900')}));
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT.parents[2],
        text=True,
        capture_output=True,
        check=True,
    )
    observed = json.loads(completed.stdout)
    from urllib.parse import parse_qs, urlparse

    activity = urlparse(observed["routes"][0])
    assert activity.path == "/api/v2/activity/daily"
    assert parse_qs(activity.query) == {"since": ["2026-11-07"], "until": ["2026-11-07"]}
    assert observed["screenshots"] == [
        "fixture/initial",
        "empty/initial",
        "narrow-keyboard/initial",
    ]
    assert observed["initialFailure"]["status"] == 503
    assert observed["initialRecovery"]["status"] == 200
    assert observed["initial"]["body"]["metrics"][0]["total_value"] == 9
    assert observed["refreshed"]["body"]["metrics"][0]["total_value"] == 26
    assert observed["refreshed"]["body"]["metrics"][0]["last_seen"] == "2026-11-07 12:04:00"
    assert observed["priorGood"]["status"] == 200
    assert observed["refreshFailed"]["status"] == 503
    assert observed["refreshRecovered"]["body"]["metrics"][0]["total_value"] == 26
    assert len(observed["bounded"]["body"]["metrics"]) == 200
    assert len(observed["maximum"]["body"]["metrics"]) == 500
    assert len({row["attrs"]["group"] for row in observed["bounded"]["body"]["metrics"]}) == 200


def test_omitted_diagnostic_traces_do_not_consume_export_budget(tmp_path, monkeypatch):
    (tmp_path / "browser").mkdir()
    (tmp_path / "browser/observation.json").write_text('{"execution_complete":true}')
    trace = tmp_path / "browser/real.trace.zip"
    trace.write_bytes(b"diagnostic" * 1024)
    monkeypatch.setattr(worker, "MAX_EVIDENCE_BYTES", 100)
    manifest = worker.evidence_manifest(tmp_path)
    assert [row["path"] for row in manifest] == ["browser/observation.json"]
    envelope = worker.export_envelope(tmp_path, {"semantic_verdict": "unassessed"})
    assert envelope["omitted_diagnostics"][0]["bytes"] == trace.stat().st_size


def test_loaded_range_transition_fixture_distinguishes_old_scope_failure_and_recovery():
    """The old row must distinguish retained seven-day data from a 24-hour read."""
    script = """
import { fixtureResponse, rangeTransitionBinding }
  from './examples/document/operational-metrics/downstream/scenarios.mjs';
const metrics = [{metric_name:'codex.memory.startup',attrs:{state:'succeeded'},
  occurrences:2,total_value:9,last_seen:'2026-11-07 12:00:00'}];
const read = phase => fixtureResponse(metrics,'range-transition',
  'http://localhost/api/v2/metrics?limit=200',phase,{rangeWindows:{wide_hours:168,narrow_hours:24}});
const alternate = fixtureResponse(metrics,'range-transition',
  'http://localhost/api/v2/metrics?limit=200','wide-loaded',{rangeWindows:{wide_hours:6,narrow_hours:1}});
const base = {range:[{}],range_return:[{}]};
console.log(JSON.stringify({initial:read('initial'),wide:read('wide-loaded'),failed:read('narrow-failed'),recovered:read('narrow-recovered'),
  alternate, missingPair:rangeTransitionBinding({range:[{}]}),
  missingBounds:rangeTransitionBinding(base),
  reversed:rangeTransitionBinding({...base,range_windows:{wide_hours:1,narrow_hours:6}}),
  valid:rangeTransitionBinding({...base,range_windows:{wide_hours:6,narrow_hours:1}})}));
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT.parents[2],
        text=True,
        capture_output=True,
        check=True,
    )
    observed = json.loads(completed.stdout)
    wide_rows = observed["wide"]["body"]["metrics"]
    old_rows = [row for row in wide_rows if row["last_seen"] == "2026-11-04 12:05:00"]
    assert len(old_rows) == 1
    assert old_rows[0]["attrs"]["state"] == "skipped_rate_limit"
    assert old_rows[0]["total_value"] == 99
    assert observed["failed"]["status"] == 503
    assert observed["recovered"]["status"] == 200
    assert old_rows[0] not in observed["initial"]["body"]["metrics"]
    assert old_rows[0] not in observed["recovered"]["body"]["metrics"]
    alternate_old = observed["alternate"]["body"]["metrics"][-1]
    assert "2026-11-07 06:05:00" < alternate_old["last_seen"] < "2026-11-07 11:05:00"
    assert observed["valid"] == {
        "applicable": True,
        "windows": {"wide_hours": 6, "narrow_hours": 1},
    }
    for variant in ("missingPair", "missingBounds", "reversed"):
        assert observed[variant]["applicable"] is False
        assert "semantic review" in observed[variant]["reason"]


@pytest.mark.parametrize("design", ["monitor-table", "analytics-cards"])
def test_authored_range_change_clears_previous_scope_before_starting_new_read(design):
    """Run only our authored event handler; no captured app code or DOM is loaded."""
    component = controls.control_bytes(design, "none").decode()
    handler = re.search(r"onchange=\{\(event\) => \{(.*?)\}\}", component, re.DOTALL)
    assert handler
    script = """
const handler = new Function('event','rows','fetched','hours','refresh',
  process.argv[1] + '; return {rows,fetched,hours};');
let reads = 0;
const result = handler({currentTarget:{value:'24'}},
  [{last_seen:'2026-11-04 12:05:00',total_value:99}], 'prior-fetch', '168', () => reads++);
console.log(JSON.stringify({...result,reads}));
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script, handler.group(1)],
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(completed.stdout) == {"rows": [], "fetched": "", "hours": "24", "reads": 1}


def test_export_failure_retains_browser_diagnostics_and_sizes_without_claiming_success(tmp_path):
    (tmp_path / "browser").mkdir()
    observation = {
        "execution_complete": True,
        "scenarios": [{"id": "fixture", "status": "observed"}],
    }
    (tmp_path / "browser/observation.json").write_text(json.dumps(observation))
    (tmp_path / "browser/real.trace.zip").write_bytes(b"explicitly omitted")
    result = {
        "execution_status": "observed",
        "browser_execution_complete": True,
        "files": [{"path": "temporary-path-only"}],
        "semantic_verdict": "unassessed",
    }
    failure = worker.export_failure(tmp_path, result, ValueError("compressed budget exceeded"))
    assert failure["execution_status"] == "incomplete"
    assert failure["full_artifact_export_retained"] is False
    assert failure["summary"]["execution_status"] == "incomplete"
    assert failure["summary"]["browser_execution_complete"] is True
    assert "files" not in failure["summary"]
    assert failure["diagnostics"]["browser"]["raw_observation"] == observation
    assert failure["diagnostics"]["omitted_trace_bytes"] == len(b"explicitly omitted")
    assert failure["diagnostics"]["file_count"] == 2
    assert "base64" not in failure
