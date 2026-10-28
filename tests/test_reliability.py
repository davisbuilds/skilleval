"""Real files and native artifact contracts; no model or fixture execution."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from test_judge import document, run_directory

from skilleval import evidence, judge, reliability
from skilleval.errors import SkillEvalError

CORPUS = Path(__file__).resolve().parents[1] / "examples/reliability/review-v1"


def prepared(tmp_path: Path, *, investigation: bool = True) -> tuple[Path, dict[str, Any]]:
    bundle = tmp_path / "source-bundle"
    evidence.snapshot(CORPUS, bundle)
    destination = tmp_path / "prepared"
    return destination, reliability.prepare_reliability(
        bundle, destination, investigation=investigation
    )


def native_record(
    tmp_path: Path,
    root: Path,
    preparation: dict[str, Any],
    name: str,
    *,
    order: str = "original",
    status: str = "completed",
    runtime: str = "synthetic-runtime-v1",
    compact: bool = False,
    mutations: dict[str, str | None] | None = None,
    native_changes: dict[str, Any] | None = None,
    remove_fields: tuple[str, ...] = (),
) -> Path:
    receipt = preparation["preparations"][order]
    bundle = root / "evidence" / order
    result = document(bundle, receipt)
    result["result_id"] = name
    for judgment in list(result["judgments"]):
        if mutations and judgment["probe_id"] in mutations:
            change = mutations[judgment["probe_id"]]
            if change is None:
                result["judgments"].remove(judgment)
            else:
                judgment["verdict"] = change
    run = run_directory(tmp_path, receipt, result, status=status, run_name=name)
    run_receipt = json.loads((run / "native-run.json").read_text())
    run_receipt["identity"] = {"runtime": {"test-runtime": runtime}, "sources": {"test": "a" * 64}}
    run_receipt.update(
        {
            "image": {
                "path": "/synthetic/image.tar",
                "sha256": "b" * 64,
                "size": 10240,
                "config_sha256": "c" * 64,
                "layer_count": 2,
            },
            "codex_identity": {
                "observed_version": "0.164.0",
                "baseline_version": "0.164.0",
                "version_policy": "stable-same-major-at-or-after-baseline",
                "baseline_match": True,
            },
            "catalog_sha256": "d" * 64,
            "gateway_limits": {
                "max_requests": 32,
                "max_body_bytes": 4194304,
                "timeout_seconds": 120,
                "max_concurrent_requests": 1,
                "lifetime_seconds": 900,
            },
            "native_timeout_seconds": 600,
            "artifact_limits": {"bytes": 16777216, "files": 1000},
        }
    )
    for key, value in (native_changes or {}).items():
        target = run_receipt
        parts = key.split(".")
        for part in parts[:-1]:
            target = target[part]
        target[parts[-1]] = value
    for key in remove_fields:
        target = run_receipt
        parts = key.split(".")
        for part in parts[:-1]:
            target = target[part]
        target.pop(parts[-1])
    if compact:
        audit = json.dumps(run_receipt).encode()
        original_identity = evidence._json_bytes(run_receipt["identity"])
        run_receipt["identity"] = {
            "bytes": len(original_identity),
            "sha256": hashlib.sha256(original_identity).hexdigest(),
            "audit_field": "identity",
        }
        (run / "native-audit.json").write_bytes(audit)
        run_receipt["audit"] = {
            "path": "native-audit.json",
            "bytes": len(audit),
            "sha256": hashlib.sha256(audit).hexdigest(),
        }
    (run / "native-run.json").write_text(json.dumps(run_receipt))
    recorded = judge.record_agentic_calibration(
        bundle, root / "packets" / order, run, tmp_path / "records"
    )
    return Path(recorded["path"]).parent


def observations(tmp_path: Path, receipt: dict[str, Any], attempts: list[dict[str, Any]]) -> Path:
    path = tmp_path / "observations.json"
    path.write_text(json.dumps({"plan_sha256": receipt["plan_sha256"], "attempts": attempts}))
    return path


def test_unresolved_and_missing_are_not_resolved_stability(tmp_path: Path) -> None:
    root, receipt = prepared(tmp_path)
    mutations = {"base-0": "unresolved", "base-1": None}
    first = native_record(tmp_path, root, receipt, "first", mutations=mutations)
    second = native_record(tmp_path, root, receipt, "second", mutations=mutations)
    compared = reliability.compare_reliability(
        root,
        observations(
            tmp_path,
            receipt,
            [
                {"attempt_id": "a", "slot_id": "repeat-a", "record_dir": str(first)},
                {"attempt_id": "b", "slot_id": "repeat-b", "record_dir": str(second)},
            ],
        ),
    )
    repeat = compared["cross_session"][0]
    assert repeat["opportunity_count"] == 14
    assert repeat["stable_resolved_count"] == 12
    assert repeat["both_unresolved_count"] == 1
    assert repeat["missing_count"] == 1


def test_preparation_preserves_original_and_changes_only_probe_order(tmp_path: Path) -> None:
    before = {
        path.relative_to(CORPUS): path.read_bytes() for path in CORPUS.rglob("*") if path.is_file()
    }
    root, receipt = prepared(tmp_path)
    original = json.loads((root / "evidence/original/suite.json").read_text())
    reversed_suite = json.loads((root / "evidence/reversed/suite.json").read_text())
    assert original["probes"] == list(reversed(reversed_suite["probes"]))
    assert receipt["session_count"] == 3
    assert receipt["pair_count"] == 12
    assert (
        receipt["preparations"]["original"]["packet_sha256"]
        != receipt["preparations"]["reversed"]["packet_sha256"]
    )
    assert not (root / "packets/original/suite.json").exists()
    assert not (root / "packets/original/reliability.json").exists()
    for name, content in before.items():
        assert (CORPUS / name).read_bytes() == content
        assert (root / "evidence/original" / name).read_bytes() == content
        if str(name) != "suite.json":
            assert (root / "evidence/reversed" / name).read_bytes() == content
    with pytest.raises(SkillEvalError, match="fresh"):
        reliability.prepare_reliability(tmp_path / "source-bundle", root)
    assert (root / "reliability-plan.json").exists()


def test_positive_control_detects_stability_and_stable_wrong_answers_separately(
    tmp_path: Path,
) -> None:
    root, receipt = prepared(tmp_path)
    first = native_record(tmp_path, root, receipt, "first")
    second = native_record(tmp_path, root, receipt, "second")
    reverse = native_record(tmp_path, root, receipt, "reverse", order="reversed")
    rows = [
        {"attempt_id": name, "slot_id": slot, "record_dir": str(record)}
        for name, slot, record in [
            ("a", "repeat-a", first),
            ("b", "repeat-b", second),
            ("c", "order-reversed", reverse),
        ]
    ]
    result = reliability.compare_reliability(root, observations(tmp_path, receipt, rows))
    assert result["completed_count"] == 3
    assert result["missing_slot_ids"] == []
    assert all(row["stable_resolved_count"] == 14 for row in result["cross_session"])
    assert all(
        row["stable_resolved_count"] == row["opportunity_count"] for row in result["invariance"]
    )
    assert result["independent_audit"] == "required-pending"
    # A consistently wrong answer is stable; authored-label disagreement remains visible.
    wrong = {
        probe["probe_id"]: "unsupported"
        for probe in json.loads((root / "evidence/original/suite.json").read_text())["probes"]
        if probe["probe_id"].endswith("-0")
    }
    bad_first = native_record(tmp_path, root, receipt, "wrong-first", mutations=wrong)
    bad_second = native_record(tmp_path, root, receipt, "wrong-second", mutations=wrong)
    rows[0]["record_dir"], rows[1]["record_dir"] = str(bad_first), str(bad_second)
    compared = reliability.compare_reliability(root, observations(tmp_path, receipt, rows[:2]))
    assert compared["cross_session"][0]["stable_resolved_count"] == 14
    assert compared["attempts"][0]["authored_label_comparison"]["disagreement_count"] == 7


def test_changed_verdicts_and_every_retry_remain_visible(tmp_path: Path) -> None:
    root, receipt = prepared(tmp_path)
    first = native_record(tmp_path, root, receipt, "first")
    second = native_record(
        tmp_path, root, receipt, "second", mutations={"paraphrase-0": "unsupported"}
    )
    retry = native_record(tmp_path, root, receipt, "retry")
    result = reliability.compare_reliability(
        root,
        observations(
            tmp_path,
            receipt,
            [
                {"attempt_id": "a", "slot_id": "repeat-a", "record_dir": str(first)},
                {"attempt_id": "b", "slot_id": "repeat-b", "record_dir": str(second)},
                {"attempt_id": "b-retry", "slot_id": "repeat-b", "record_dir": str(retry)},
            ],
        ),
    )
    repeat = [row for row in result["cross_session"] if row["family"] == "repeat"]
    assert [row["changed_resolved_count"] for row in repeat] == [1, 0]
    paraphrase = next(
        row
        for row in result["invariance"]
        if row["attempt_id"] == "b" and row["family"] == "equivalent-paraphrase"
    )
    assert paraphrase["changed_resolved_count"] == 1
    assert paraphrase["probe_pairs"][0]["left_verdict"] == "supported"
    assert paraphrase["probe_pairs"][0]["right_verdict"] == "unsupported"


def test_incompatible_runtime_identities_are_not_compared(tmp_path: Path) -> None:
    root, receipt = prepared(tmp_path)
    first = native_record(tmp_path, root, receipt, "first")
    second = native_record(tmp_path, root, receipt, "second", runtime="different-runtime")
    compared = reliability.compare_reliability(
        root,
        observations(
            tmp_path,
            receipt,
            [
                {"attempt_id": "a", "slot_id": "repeat-a", "record_dir": str(first)},
                {"attempt_id": "b", "slot_id": "repeat-b", "record_dir": str(second)},
            ],
        ),
    )
    repeat = compared["cross_session"][0]
    assert compared["completed_count"] == 2
    assert repeat["compatible_completed_pair"] is False
    assert repeat["unavailable_reason"] == "incompatible-native-identity"
    assert repeat["resolved_pair_count"] == 0
    assert repeat["missing_count"] == 0
    assert repeat["incompatible_pair_count"] == 14


def test_failed_and_missing_denominators_never_become_clean_or_stable(tmp_path: Path) -> None:
    root, receipt = prepared(tmp_path)
    failed = native_record(tmp_path, root, receipt, "failed", status="failed")
    compared = reliability.compare_reliability(
        root,
        observations(
            tmp_path,
            receipt,
            [
                {"attempt_id": "a", "slot_id": "repeat-a", "record_dir": str(failed)},
                {
                    "attempt_id": "b",
                    "slot_id": "repeat-b",
                    "declared_failure": "Invocation interrupted before any record.",
                },
            ],
        ),
    )
    assert compared["failed_count"] == 2
    assert compared["completed_count"] == 0
    assert compared["missing_slot_ids"] == ["order-reversed"]
    assert all(row["stable_resolved_count"] == 0 for row in compared["invariance"])
    assert all(
        row["missing_count"] == row["opportunity_count"] for row in compared["cross_session"]
    )
    empty = reliability.compare_reliability(root, observations(tmp_path, receipt, []))
    assert len(empty["missing_slot_ids"]) == 3
    assert len(empty["invariance"]) == 12
    assert all(row["resolved_pair_count"] == 0 for row in empty["invariance"])


@pytest.mark.parametrize("mutation", ["result", "artifact", "packet", "missing-identity"])
def test_record_drift_is_rejected_without_dropping_denominator(
    tmp_path: Path, mutation: str
) -> None:
    root, receipt = prepared(tmp_path)
    record = native_record(tmp_path, root, receipt, "first")
    if mutation == "result":
        document = json.loads((record / "result.json").read_text())
        document["judgments"][0]["verdict"] = "unsupported"
        (record / "result.json").write_text(json.dumps(document))
    elif mutation == "artifact":
        (record / "artifacts.tar").write_bytes(b"invalid")
    elif mutation == "packet":
        (record / "packet/cases/base/source/invoice.py").write_text("changed\n")
    else:
        document = json.loads((record / "native-run.json").read_text())
        document.pop("identity")
        (record / "native-run.json").write_text(json.dumps(document))
    compared = reliability.compare_reliability(
        root,
        observations(
            tmp_path,
            receipt,
            [
                {"attempt_id": "a", "slot_id": "repeat-a", "record_dir": str(record)},
            ],
        ),
    )
    assert compared["invalid_count"] == 1
    assert compared["completed_count"] == 0
    assert compared["cross_session"][0]["missing_count"] == 14
    assert compared["attempts"][0]["reason"]


def test_record_reuse_and_wrong_slot_are_invalid(tmp_path: Path) -> None:
    root, receipt = prepared(tmp_path)
    record = native_record(tmp_path, root, receipt, "first")
    compared = reliability.compare_reliability(
        root,
        observations(
            tmp_path,
            receipt,
            [
                {"attempt_id": "a", "slot_id": "repeat-a", "record_dir": str(record)},
                {"attempt_id": "b", "slot_id": "repeat-b", "record_dir": str(record)},
                {"attempt_id": "c", "slot_id": "order-reversed", "record_dir": str(record)},
            ],
        ),
    )
    assert compared["completed_count"] == 1
    assert compared["invalid_count"] == 2
    assert "reused" in compared["attempts"][1]["reason"]
    assert "differs" in compared["attempts"][2]["reason"]


def test_observation_manifest_and_preparation_identity_are_strict(tmp_path: Path) -> None:
    root, receipt = prepared(tmp_path)
    path = observations(tmp_path, receipt, [])
    data = json.loads(path.read_text())
    data["plan_sha256"] = "0" * 64
    path.write_text(json.dumps(data))
    with pytest.raises(SkillEvalError, match="preparation identity"):
        reliability.compare_reliability(root, path)
    path.write_text('{"plan_sha256":"' + receipt["plan_sha256"] + '","attempts":[],"attempts":[]}')
    with pytest.raises(SkillEvalError, match="duplicate JSON"):
        reliability.compare_reliability(root, path)
    path = observations(tmp_path, receipt, [])
    (root / "evidence/reversed/suite.json").write_text("{}")
    with pytest.raises(SkillEvalError):
        reliability.compare_reliability(root, path)


def test_static_packets_and_compact_runtime_identity_use_native_contract(tmp_path: Path) -> None:
    root, receipt = prepared(tmp_path, investigation=False)
    first = native_record(tmp_path, root, receipt, "first", compact=True)
    second = native_record(tmp_path, root, receipt, "second")
    compared = reliability.compare_reliability(
        root,
        observations(
            tmp_path,
            receipt,
            [
                {"attempt_id": "a", "slot_id": "repeat-a", "record_dir": str(first)},
                {"attempt_id": "b", "slot_id": "repeat-b", "record_dir": str(second)},
            ],
        ),
    )
    assert receipt["preparations"]["original"]["investigation"] is False
    assert receipt["preparations"]["original"]["rubric_version"] == "calibration-static-v1"
    assert compared["completed_count"] == 2
    assert compared["cross_session"][0]["stable_resolved_count"] == 14
    assert compared["cross_session"][0]["compatible_completed_pair"] is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("image.sha256", "e" * 64),
        ("image.config_sha256", "f" * 64),
        ("image.size", 20480),
        ("image.layer_count", 3),
        ("codex_identity.observed_version", "0.165.0"),
        ("codex_identity.baseline_version", "0.163.0"),
        ("codex_identity.version_policy", "another-policy"),
        ("codex_identity.baseline_match", False),
        ("catalog_sha256", "e" * 64),
        ("native_timeout_seconds", 601),
        ("gateway_limits.max_requests", 31),
        ("gateway_limits.max_body_bytes", 4194305),
        ("gateway_limits.timeout_seconds", 121),
        ("gateway_limits.max_concurrent_requests", 2),
        ("gateway_limits.lifetime_seconds", 899),
        ("artifact_limits.bytes", 16777215),
        ("artifact_limits.files", 999),
    ],
)
def test_actual_execution_inputs_must_match_for_stability(
    tmp_path: Path, field: str, value: Any
) -> None:
    root, receipt = prepared(tmp_path)
    first = native_record(tmp_path, root, receipt, "first")
    second = native_record(tmp_path, root, receipt, "second", native_changes={field: value})
    compared = reliability.compare_reliability(
        root,
        observations(
            tmp_path,
            receipt,
            [
                {"attempt_id": "a", "slot_id": "repeat-a", "record_dir": str(first)},
                {"attempt_id": "b", "slot_id": "repeat-b", "record_dir": str(second)},
            ],
        ),
    )
    repeat = compared["cross_session"][0]
    assert compared["completed_count"] == 2
    assert repeat["compatible_completed_pair"] is False
    assert repeat["stable_resolved_count"] == 0
    assert repeat["missing_count"] == 0
    assert repeat["incompatible_pair_count"] == 14
    assert repeat["probe_pairs"][0]["left_verdict"] == "supported"
    assert repeat["probe_pairs"][0]["right_verdict"] == "supported"


@pytest.mark.parametrize(
    "field",
    [
        "image",
        "image.sha256",
        "image.config_sha256",
        "image.size",
        "image.layer_count",
        "codex_identity",
        "codex_identity.observed_version",
        "codex_identity.baseline_version",
        "codex_identity.version_policy",
        "codex_identity.baseline_match",
        "catalog_sha256",
        "native_timeout_seconds",
        "gateway_limits",
        "gateway_limits.max_requests",
        "gateway_limits.max_body_bytes",
        "gateway_limits.timeout_seconds",
        "gateway_limits.max_concurrent_requests",
        "gateway_limits.lifetime_seconds",
        "artifact_limits",
        "artifact_limits.bytes",
        "artifact_limits.files",
    ],
)
def test_missing_execution_identity_is_invalid_even_when_missing_on_both_sides(
    tmp_path: Path, field: str
) -> None:
    root, receipt = prepared(tmp_path)
    first = native_record(tmp_path, root, receipt, "first", remove_fields=(field,))
    second = native_record(tmp_path, root, receipt, "second", remove_fields=(field,))
    compared = reliability.compare_reliability(
        root,
        observations(
            tmp_path,
            receipt,
            [
                {"attempt_id": "a", "slot_id": "repeat-a", "record_dir": str(first)},
                {"attempt_id": "b", "slot_id": "repeat-b", "record_dir": str(second)},
            ],
        ),
    )
    assert compared["invalid_count"] == 2
    assert compared["completed_count"] == 0
    assert compared["cross_session"][0]["resolved_pair_count"] == 0


def test_compact_identity_without_audit_is_an_invalid_attempt_not_a_batch_abort(
    tmp_path: Path,
) -> None:
    root, receipt = prepared(tmp_path)
    record = native_record(tmp_path, root, receipt, "first", compact=True)
    receipt_file = record / "native-run.json"
    data = json.loads(receipt_file.read_text())
    data.pop("audit")
    receipt_file.write_text(json.dumps(data))
    compared = reliability.compare_reliability(
        root,
        observations(
            tmp_path,
            receipt,
            [
                {"attempt_id": "a", "slot_id": "repeat-a", "record_dir": str(record)},
            ],
        ),
    )
    assert compared["invalid_count"] == 1
    assert "audit" in compared["attempts"][0]["reason"]


def test_one_missing_session_keeps_completed_side_observations(tmp_path: Path) -> None:
    root, receipt = prepared(tmp_path)
    record = native_record(tmp_path, root, receipt, "first")
    compared = reliability.compare_reliability(
        root,
        observations(
            tmp_path,
            receipt,
            [
                {"attempt_id": "a", "slot_id": "repeat-a", "record_dir": str(record)},
            ],
        ),
    )
    repeat = compared["cross_session"][0]
    assert repeat["probe_pairs"][0]["left_verdict"] == "supported"
    assert repeat["probe_pairs"][0]["right_verdict"] is None
    assert repeat["left_missing_count"] == 0
    assert repeat["right_missing_count"] == 14
    assert repeat["incompatible_pair_count"] == 0
    assert repeat["stable_resolved_count"] == 0


def test_compact_execution_metadata_must_match_hash_bound_audit(tmp_path: Path) -> None:
    root, receipt = prepared(tmp_path)
    record = native_record(tmp_path, root, receipt, "first", compact=True)
    receipt_file = record / "native-run.json"
    data = json.loads(receipt_file.read_text())
    data["image"]["size"] += 1
    receipt_file.write_text(json.dumps(data))
    compared = reliability.compare_reliability(
        root,
        observations(
            tmp_path,
            receipt,
            [
                {"attempt_id": "a", "slot_id": "repeat-a", "record_dir": str(record)},
            ],
        ),
    )
    assert compared["invalid_count"] == 1
    assert "differ from retained audit" in compared["attempts"][0]["reason"]


def test_identical_image_content_at_different_paths_remains_compatible(tmp_path: Path) -> None:
    root, receipt = prepared(tmp_path)
    first = native_record(tmp_path, root, receipt, "first")
    second = native_record(
        tmp_path, root, receipt, "second", native_changes={"image.path": "/another/image.tar"}
    )
    compared = reliability.compare_reliability(
        root,
        observations(
            tmp_path,
            receipt,
            [
                {"attempt_id": "a", "slot_id": "repeat-a", "record_dir": str(first)},
                {"attempt_id": "b", "slot_id": "repeat-b", "record_dir": str(second)},
            ],
        ),
    )
    assert compared["cross_session"][0]["compatible_completed_pair"] is True
    assert compared["cross_session"][0]["stable_resolved_count"] == 14
