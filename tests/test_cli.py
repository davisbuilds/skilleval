import json
import subprocess
import sys
from pathlib import Path

import pytest

from skilleval import cli


def invoke(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "skilleval", *args], capture_output=True, text=True, check=False
    )


def test_help_never_needs_a_runtime() -> None:
    result = invoke("--help")
    assert result.returncode == 0
    assert "import-review" in result.stdout
    assert not result.stderr


def test_schema_is_structured_and_versioned() -> None:
    result = invoke("schema", "assessment")
    assert result.returncode == 0
    assert json.loads(result.stdout)["properties"]["schema_version"]["const"] == 1


def test_record_validation_does_not_echo_private_input(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    path.write_text('{"task_id":"PRIVATE_SENTINEL", "schema_version":99}')
    result = invoke("validate", "task", str(path))
    assert result.returncode == 2
    assert not result.stdout
    assert "PRIVATE_SENTINEL" not in result.stderr
    assert json.loads(result.stderr)["error"] == "invalid record"


def test_duplicate_control_keys_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    path.write_text('{"schema_version":1,"schema_version":99}')
    result = invoke("validate", "task", str(path))
    assert result.returncode == 2
    assert json.loads(result.stderr)["error"] == "duplicate JSON object key"


def test_numeric_overflow_is_rejected_before_record_validation(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    path.write_text('{"value":1e999}')
    result = invoke("validate", "task", str(path))
    assert result.returncode == 2
    assert json.loads(result.stderr)["error"] == "non-finite JSON number"


def test_partial_qualification_is_a_shell_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "dispatch", lambda _: {"status": "partial", "admitted": False})
    assert cli.main(["qualification", "run", "plan.json", "--output", "result"]) == 1
    assert json.loads(capsys.readouterr().out)["admitted"] is False


def test_inspection_is_bounded_for_large_inventory(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    for index in range(20):
        (source / f"file-{index}.txt").write_text("example\n")
    bundle = tmp_path / "bundle"
    captured = invoke("capture", str(source), "--output", str(bundle))
    assert captured.returncode == 0, captured.stderr
    digest = json.loads(captured.stdout)["manifest_sha256"]
    inspected = invoke("inspect", str(bundle), "--sha256", digest)
    assert inspected.returncode == 0, inspected.stderr
    result = json.loads(inspected.stdout)
    assert result["file_count"] == 20
    assert "manifest" not in result
    assert result["scope"] == "captured-byte-integrity"
    assert result["expected_digest_checked"] is True


def test_control_symlinks_and_fifos_are_rejected_without_hanging(tmp_path: Path) -> None:
    import os

    target = tmp_path / "target.json"
    target.write_text("{}")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    fifo = tmp_path / "fifo.json"
    os.mkfifo(fifo)
    for path in (link, fifo):
        result = subprocess.run(
            [sys.executable, "-m", "skilleval", "validate", "task", str(path)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        assert result.returncode == 2


def test_documented_offline_workflow(tmp_path: Path) -> None:
    """Exercise the public CLI against the self-contained demonstration data."""
    examples = Path(__file__).resolve().parents[1] / "examples" / "review"
    bundle = tmp_path / "bundle"
    captured = invoke(
        "import-review", str(examples / "legacy"), "--case", "demo", "--output", str(bundle)
    )
    assert captured.returncode == 0, captured.stderr
    receipt = json.loads(captured.stdout)
    digest = receipt["manifest_sha256"]
    verified = invoke("inspect", str(bundle), "--sha256", digest)
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout)["expected_digest_checked"] is True

    packet = tmp_path / "packet"
    prepared = invoke("review", "prepare", str(bundle), "--output", str(packet))
    assert prepared.returncode == 0, prepared.stderr
    assert json.loads(prepared.stdout)["report_count"] == 2
    assert (packet / "case" / "source" / "totals.py").is_file()
    assert not (packet / "case" / "oracle.md").exists()
    assert not (packet / "provenance").exists()

    record = json.loads((examples / "assessment.template.json").read_text())
    record["evidence_sha256"] = digest
    assessment = tmp_path / "assessment.json"
    assessment.write_text(json.dumps(record))
    output = tmp_path / "assessments"
    arguments = ("review", "record", str(bundle), str(assessment), "--output", str(output))
    recorded = invoke(*arguments)
    assert recorded.returncode == 0, recorded.stderr
    stored = output / "demo-assessment-1.json"
    before = stored.read_bytes()
    assert json.loads(before)["evidence_sha256"] == digest
    assert invoke(*arguments).returncode == 2  # cannot replace a prior judgment
    assert stored.read_bytes() == before

    controls = tmp_path / "controls.json"
    controls.write_text('[["python3", "--version"]]')
    plan_path = tmp_path / "plan.json"
    planned = invoke(
        "qualification",
        "plan",
        str(bundle),
        "--image",
        "docker.io/library/python@sha256:" + "a" * 64,
        "--commands",
        str(controls),
        "--output",
        str(plan_path),
    )
    assert planned.returncode == 0, planned.stderr
    assert json.loads(plan_path.read_text())["admitted"] is False
    assert json.loads(plan_path.read_text())["bundle_sha256"] == digest
    # A synthetic digest only tests planning; never ask a runtime to acquire it.
    assert invoke("inspect", str(bundle), "--sha256", digest).returncode == 0
