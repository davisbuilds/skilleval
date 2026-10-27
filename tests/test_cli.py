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


def test_native_judge_requires_declared_policy_before_any_runtime_access() -> None:
    result = invoke(
        "native",
        "qualify",
        "missing-bundle",
        "--sha256",
        "0" * 64,
        "--prompt",
        "missing-prompt",
        "--mode",
        "judge",
        "--runtime",
        "missing-runtime",
        "--image-archive",
        "missing-image",
        "--image-sha256",
        "0" * 64,
        "--catalog",
        "missing-catalog",
        "--auth-file",
        "missing-auth",
        "--output",
        "missing-out",
    )
    assert result.returncode == 2
    assert "judge mode requires --judge-policy" in result.stderr
    assert not result.stdout


def test_native_judge_packet_cli_preserves_hidden_labels(tmp_path: Path) -> None:
    from skilleval.evidence import snapshot

    corpus = Path(__file__).parents[1] / "examples/calibration/review-v1"
    bundle = tmp_path / "bundle"
    snapshot(corpus, bundle)
    packet = tmp_path / "packet"
    prepared = invoke("judge", "prepare-calibration", str(bundle), "--output", str(packet))
    assert prepared.returncode == 0, prepared.stderr
    receipt = json.loads(prepared.stdout)
    assert receipt["investigation"] is True
    assert not (packet / "suite.json").exists()
    assert "expected" not in (packet / "probes.json").read_text()
    frozen = invoke("capture", str(packet), "--output", str(tmp_path / "frozen"))
    assert frozen.returncode == 0, frozen.stderr
    assert json.loads(frozen.stdout)["manifest_sha256"] == receipt["input_digest"]


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


@pytest.mark.parametrize("status", ["partial", "timed-out", "failed"])
def test_partial_qualification_is_a_shell_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], status: str
) -> None:
    monkeypatch.setattr(cli, "dispatch", lambda _: {"status": status, "admitted": False})
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


def test_calibration_commands_preserve_unknown_coverage_and_append_results(tmp_path: Path) -> None:
    corpus = Path(__file__).resolve().parents[1] / "examples/calibration/review-v1"
    bundle = tmp_path / "calibration-bundle"
    captured = invoke("capture", str(corpus), "--output", str(bundle))
    assert captured.returncode == 0, captured.stderr
    packet = tmp_path / "packet"
    prepared = invoke("calibration", "prepare", str(bundle), "--output", str(packet))
    assert prepared.returncode == 0, prepared.stderr
    receipt = json.loads(prepared.stdout)
    assert receipt["probe_count"] == 12
    assert not (packet / "suite.json").exists()
    assert "expected" not in json.loads((packet / "probes.json").read_text())[0]

    empty = invoke("calibration", "compare", str(bundle))
    assert empty.returncode == 0, empty.stderr
    assert json.loads(empty.stdout)["unassessed_probe_count"] == 12

    authored = json.loads((bundle / "suite.json").read_text())["probes"][0]
    document = {
        "schema_version": 1,
        "result_id": "cli-control",
        "suite_id": receipt["suite_id"],
        "evidence_sha256": receipt["evidence_sha256"],
        "packet_sha256": receipt["packet_sha256"],
        "rubric_version": "calibration-review-v1",
        "assessor_kind": "human",
        "assessor": "cli-test-control",
        "judgments": [
            {
                "probe_id": authored["probe_id"],
                "verdict": authored["expected"],
                "reasoning": authored["rationale"],
                "citations": authored["citations"],
            }
        ],
        "limitations": ["Tests only CLI wiring; no semantic judge ran."],
    }
    result_file = tmp_path / "input-result.json"
    result_file.write_text(json.dumps(document))
    validated = invoke("validate", "calibration-result", str(result_file))
    assert validated.returncode == 0, validated.stderr
    assert json.loads(validated.stdout)["status"] == "valid-structure"
    schema = invoke("schema", "calibration-result")
    assert schema.returncode == 0, schema.stderr
    assert "packet_sha256" in json.loads(schema.stdout)["required"]

    output = tmp_path / "records"
    arguments = ("calibration", "record", str(bundle), str(result_file), "--output", str(output))
    recorded = invoke(*arguments)
    assert recorded.returncode == 0, recorded.stderr
    assert json.loads(recorded.stdout)["status"] == "recorded"
    stored = output / "cli-control.json"
    before = stored.read_bytes()
    assert invoke(*arguments).returncode == 2
    assert stored.read_bytes() == before
    compared = invoke("calibration", "compare", str(bundle), str(stored))
    assert compared.returncode == 0, compared.stderr
    comparison = json.loads(compared.stdout)
    assert comparison["results"][0]["agreement_count"] == 1
    assert comparison["results"][0]["missing_count"] == 11
    assert comparison["results"][0]["judgment_count"] == 1
    assert comparison["invalid_result_count"] == 0

    result_file.write_text('{"PRIVATE-SENTINEL":"invalid"}')
    invalid = invoke("calibration", "compare", str(bundle), str(result_file))
    assert invalid.returncode == 0, invalid.stderr
    assert json.loads(invalid.stdout)["invalid_result_count"] == 1
    assert "PRIVATE-SENTINEL" not in invalid.stdout


def test_workspace_prepare_requires_pin_and_reconstructs_native_history(tmp_path: Path) -> None:
    import hashlib

    def blob(value: bytes) -> str:
        return hashlib.sha1(b"blob " + str(len(value)).encode() + b"\0" + value).hexdigest()

    legacy = tmp_path / "legacy"
    case = legacy / "cases/synthetic"
    source = case / "source"
    source.mkdir(parents=True)
    (source / "value.txt").write_bytes(b"after\n")
    (source / ".openbench-review.json").write_text('{"schema":1,"patch":"review.patch"}')
    (source / "review.patch").write_text(
        "diff --git a/value.txt b/value.txt\n"
        f"index {blob(b'before\n')}..{blob(b'after\n')} 100644\n"
        "--- a/value.txt\n+++ b/value.txt\n@@ -1 +1 @@\n-before\n+after\n"
    )
    (case / "instruction.md").write_text("Review this patch without executing imported code.\n")
    (case / "oracle.md").write_text("PRIVATE-ORACLE\n")
    (legacy / "reports.json").write_text('[{"case_id":"synthetic","findings":[]}]')
    bundle = tmp_path / "bundle"
    imported = invoke("import-review", str(legacy), "--case", "synthetic", "--output", str(bundle))
    assert imported.returncode == 0, imported.stderr
    digest = json.loads(imported.stdout)["manifest_sha256"]
    target = tmp_path / "workspace"
    missing_pin = invoke("workspace", "prepare", str(bundle), "--output", str(target))
    assert missing_pin.returncode == 2
    assert not target.exists()
    wrong_pin = invoke(
        "workspace", "prepare", str(bundle), "--sha256", "0" * 64, "--output", str(target)
    )
    assert wrong_pin.returncode == 2
    assert not target.exists()
    arguments = ("workspace", "prepare", str(bundle), "--sha256", digest, "--output", str(target))
    prepared = invoke(*arguments)
    assert prepared.returncode == 0, prepared.stderr
    receipt = json.loads(prepared.stdout)
    assert receipt["manifest_sha256"] == digest
    assert receipt["reconstructed_base"] is True
    assert receipt["original_history_available"] is False
    assert (target / "value.txt").read_bytes() == b"after\n"
    assert (target / ".git").is_dir()
    assert not (target / "oracle.md").exists()
    base = subprocess.run(
        ["/usr/bin/git", "-C", str(target), "show", "base:value.txt"],
        capture_output=True,
        check=False,
    )
    assert base.returncode == 0
    assert base.stdout == b"before\n"
    assert invoke(*arguments).returncode == 2
    assert (target / "value.txt").read_bytes() == b"after\n"
