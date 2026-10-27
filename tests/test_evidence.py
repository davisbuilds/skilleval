"""Synthetic evidence only: private imported cases never enter this suite."""

import hashlib
import json
import os
from pathlib import Path

import pytest

from skilleval import evidence
from skilleval.errors import SkillEvalError


def source_tree(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    source.mkdir()
    (source / "nested").mkdir()
    (source / "nested" / "data").write_bytes(b"frozen\x00bytes")
    (source / "run").write_bytes(b"#!/bin/sh\nexit 0\n")
    (source / "run").chmod(0o755)
    return source


def rewrite_manifest(bundle: Path, transform: object) -> None:
    manifest_path = bundle / "manifest.json"
    manifest_path.chmod(0o644)
    document = json.loads(manifest_path.read_bytes())
    transform(document)  # type: ignore[operator]
    manifest_path.write_text(json.dumps(document))


def test_snapshot_copies_exact_bytes_and_preserves_execution(tmp_path: Path) -> None:
    source = source_tree(tmp_path)
    bundle = tmp_path / "bundle"
    receipt = evidence.snapshot(source, bundle)
    assert receipt["path"] == str(bundle)
    assert receipt["file_count"] == 2
    assert receipt["total_bytes"] == 29
    assert (
        receipt["manifest_sha256"]
        == hashlib.sha256((bundle / "manifest.json").read_bytes()).hexdigest()
    )
    assert (bundle / "nested" / "data").read_bytes() == b"frozen\x00bytes"
    assert os.stat(bundle / "run").st_mode & 0o111
    assert not os.stat(bundle / "nested" / "data").st_mode & 0o111
    (source / "nested" / "data").write_text("changed source")
    manifest = evidence.verify(bundle, receipt["manifest_sha256"])
    assert manifest["schema_version"] == 1
    assert manifest["kind"] == "snapshot"


@pytest.mark.parametrize("change", ["add", "remove", "mutate", "symlink", "executable"])
def test_verify_rejects_inventory_and_content_changes(tmp_path: Path, change: str) -> None:
    bundle = tmp_path / "bundle"
    receipt = evidence.snapshot(source_tree(tmp_path), bundle)
    bundle.chmod(0o755)
    target = bundle / "run"
    if change == "add":
        (bundle / "extra").write_text("extra")
    elif change == "remove":
        target.unlink()
    elif change == "mutate":
        target.chmod(0o644)
        target.write_text("altered")
    elif change == "symlink":
        target.unlink()
        target.symlink_to(tmp_path / "elsewhere")
    else:
        target.chmod(0o444)
    with pytest.raises(SkillEvalError):
        evidence.verify(bundle, receipt["manifest_sha256"])


def test_verification_checks_digest_and_manifest_paths(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    evidence.snapshot(source_tree(tmp_path), bundle)
    with pytest.raises(SkillEvalError, match="digest"):
        evidence.verify(bundle, "0" * 64)
    rewrite_manifest(bundle, lambda m: m["files"][0].update(path="../outside"))
    with pytest.raises(SkillEvalError):
        evidence.verify(bundle)


def test_snapshot_refuses_links_and_leaves_no_partial_bundle(tmp_path: Path) -> None:
    source = source_tree(tmp_path)
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("outside secret")
    (source / "link").symlink_to(sentinel)
    bundle = tmp_path / "bundle"
    with pytest.raises(SkillEvalError, match=r"link|regular"):
        evidence.snapshot(source, bundle)
    assert not bundle.exists()
    assert sentinel.read_text() == "outside secret"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["sentinel", "source"]


def test_snapshot_refuses_existing_and_nested_destination(tmp_path: Path) -> None:
    source = source_tree(tmp_path)
    with pytest.raises(SkillEvalError):
        evidence.snapshot(source, source / "nested" / "bundle")
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "sentinel").write_text("retain")
    with pytest.raises(SkillEvalError):
        evidence.snapshot(source, existing)
    assert (existing / "sentinel").read_text() == "retain"


def test_snapshot_enforces_limits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = source_tree(tmp_path)
    monkeypatch.setattr(evidence, "MAX_FILES", 1)
    with pytest.raises(SkillEvalError, match="limit"):
        evidence.snapshot(source, tmp_path / "bundle")
    assert not (tmp_path / "bundle").exists()


def test_import_preserves_report_bytes_and_selects_case(tmp_path: Path) -> None:
    root = tmp_path / "legacy"
    case = root / "cases" / "synthetic-case"
    (case / "source").mkdir(parents=True)
    (case / "instruction.md").write_text("Inspect synthetic code")
    (case / "oracle.md").write_text("Synthetic adjudication")
    (case / "source" / "example.py").write_text("x = 1\n")
    reports = (
        b'[\n {"case_id":"other","report_id":"r0"},\n '
        b'{"case_id":"synthetic-case","report_id":"r1","findings":[]}\n]\n'
    )
    (root / "reports.json").write_bytes(reports)
    bundle = tmp_path / "imported"
    receipt = evidence.import_review(root, "synthetic-case", bundle)
    manifest = evidence.verify(bundle, receipt["manifest_sha256"])
    assert manifest["kind"] == "historical_review_import"
    assert manifest["meta"]["population"] == "development"
    assert manifest["meta"]["independent_blind"] is False
    assert manifest["meta"]["historical_import"] is True
    assert manifest["meta"]["prior_chain_verified"] is False
    assert (bundle / "provenance" / "source-reports.json").read_bytes() == reports
    assert json.loads((bundle / "reports.json").read_bytes()) == [
        {"case_id": "synthetic-case", "report_id": "r1", "findings": []}
    ]
    assert (bundle / "case" / "source" / "example.py").read_text() == "x = 1\n"


@pytest.mark.parametrize("case_id", ["../outside", "/absolute", "", ".", "a/b"])
def test_import_rejects_unsafe_identifiers(tmp_path: Path, case_id: str) -> None:
    with pytest.raises(SkillEvalError):
        evidence.import_review(tmp_path, case_id, tmp_path / "bundle")


@pytest.mark.parametrize("limit", ["MAX_FILE_BYTES", "MAX_TOTAL_BYTES"])
def test_snapshot_rejects_byte_overruns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    monkeypatch.setattr(evidence, limit, 1)
    with pytest.raises(SkillEvalError, match="limit"):
        evidence.snapshot(source_tree(tmp_path), tmp_path / "bundle")
    assert not (tmp_path / "bundle").exists()
    assert not list(tmp_path.glob(".skilleval-*"))


@pytest.mark.parametrize("link_location", ["root", "directory"])
def test_source_directory_links_cannot_read_outside_tree(
    tmp_path: Path, link_location: str
) -> None:
    source = source_tree(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel").write_text("external")
    if link_location == "root":
        source = tmp_path / "linked-source"
        source.symlink_to(outside, target_is_directory=True)
    else:
        (source / "linked-directory").symlink_to(outside, target_is_directory=True)
    with pytest.raises(SkillEvalError):
        evidence.snapshot(source, tmp_path / "bundle")
    assert not (tmp_path / "bundle").exists()
    assert (outside / "sentinel").read_text() == "external"


def test_nonregular_source_refused_without_opening_fifo(tmp_path: Path) -> None:
    source = source_tree(tmp_path)
    os.mkfifo(source / "pipe")
    with pytest.raises(SkillEvalError, match="regular"):
        evidence.snapshot(source, tmp_path / "bundle")
    assert not (tmp_path / "bundle").exists()


def test_publication_never_replaces_a_concurrently_created_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = source_tree(tmp_path)
    bundle = tmp_path / "bundle"
    original_publish = evidence._publish

    def create_competing_destination(temporary: Path, destination: Path) -> None:
        destination.mkdir()
        original_publish(temporary, destination)

    monkeypatch.setattr(evidence, "_publish", create_competing_destination)
    with pytest.raises(SkillEvalError):
        evidence.snapshot(source, bundle)
    assert bundle.is_dir()
    assert list(bundle.iterdir()) == []
    assert not list(tmp_path.glob(".skilleval-*"))


@pytest.mark.parametrize("bad_path", ["/absolute", "a//b", "a/./b", "a/../b", "a\\b"])
def test_manifest_path_aliases_are_rejected(tmp_path: Path, bad_path: str) -> None:
    bundle = tmp_path / "bundle"
    evidence.snapshot(source_tree(tmp_path), bundle)
    rewrite_manifest(bundle, lambda m: m["files"][0].update(path=bad_path))
    with pytest.raises(SkillEvalError):
        evidence.verify(bundle)


def test_manifest_schema_and_duplicates_are_rejected(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    evidence.snapshot(source_tree(tmp_path), bundle)
    rewrite_manifest(bundle, lambda m: m["files"].append(m["files"][0]))
    with pytest.raises(SkillEvalError, match="duplicate"):
        evidence.verify(bundle)
    rewrite_manifest(bundle, lambda m: m.update(schema_version=2))
    with pytest.raises(SkillEvalError):
        evidence.verify(bundle)


def test_import_missing_report_does_not_leave_partial_output(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "reports.json").write_text('[{"case_id":"other"}]')
    with pytest.raises(SkillEvalError, match="no reports"):
        evidence.import_review(root, "selected", tmp_path / "bundle")
    assert not (tmp_path / "bundle").exists()
    assert not list(tmp_path.glob(".skilleval-*"))


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_manifest_rejects_nonstandard_json_constants(tmp_path: Path, constant: str) -> None:
    bundle = tmp_path / "bundle"
    evidence.snapshot(source_tree(tmp_path), bundle)
    manifest = bundle / "manifest.json"
    manifest.chmod(0o644)
    document = manifest.read_text()
    manifest.write_text(document.replace('"meta": {}', f'"meta": {{"value": {constant}}}'))
    with pytest.raises(SkillEvalError, match="JSON"):
        evidence.verify(bundle)


def test_verify_schema_errors_do_not_disclose_input(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    evidence.snapshot(source_tree(tmp_path), bundle)
    rewrite_manifest(bundle, lambda m: m.update(schema_version="PRIVATE_INPUT_MARKER"))
    with pytest.raises(SkillEvalError) as caught:
        evidence.verify(bundle)
    assert "PRIVATE_INPUT_MARKER" not in str(caught.value)
    assert "input_value" not in str(caught.value)


def test_capture_schema_errors_do_not_disclose_filenames(tmp_path: Path) -> None:
    source = source_tree(tmp_path)
    (source / "PRIVATE_INPUT_MARKER\\invalid").write_text("synthetic")
    with pytest.raises(SkillEvalError) as caught:
        evidence.snapshot(source, tmp_path / "bundle")
    assert "PRIVATE_INPUT_MARKER" not in str(caught.value)
    assert "input_value" not in str(caught.value)
    assert not (tmp_path / "bundle").exists()


@pytest.mark.parametrize("operation", ["capture", "verify"])
def test_os_errors_do_not_disclose_input_paths(tmp_path: Path, operation: str) -> None:
    private_path = tmp_path / "PRIVATE_INPUT_MARKER"
    with pytest.raises(SkillEvalError) as caught:
        if operation == "capture":
            evidence.snapshot(private_path, tmp_path / "bundle")
        else:
            evidence.verify(private_path)
    assert "PRIVATE_INPUT_MARKER" not in str(caught.value)
    assert str(tmp_path) not in str(caught.value)


@pytest.mark.parametrize("number", ["1e999", "-1e999"])
def test_import_rejects_numeric_overflow_in_report_metadata(tmp_path: Path, number: str) -> None:
    root = tmp_path / "source"
    case = root / "cases" / "selected"
    (case / "source").mkdir(parents=True)
    (case / "instruction.md").write_text("Synthetic instruction")
    (case / "oracle.md").write_text("Synthetic oracle")
    (case / "source" / "example.py").write_text("x = 1\n")
    (root / "reports.json").write_text(
        '[{"case_id":"selected","metadata":{"number":' + number + "}}]"
    )
    with pytest.raises(SkillEvalError, match="JSON"):
        evidence.import_review(root, "selected", tmp_path / "bundle")
    assert not (tmp_path / "bundle").exists()
    assert not list(tmp_path.glob(".skilleval-*"))


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")])
def test_json_serializer_rejects_nonfinite_values(number: float) -> None:
    with pytest.raises(ValueError):
        evidence._json_bytes({"number": number})
