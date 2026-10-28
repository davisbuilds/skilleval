"""Real files and CLI operations, without VMs, credentials or provider calls."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from test_cli import invoke
from test_qualification import docker_archive

from skilleval.qualification import _image_archive
from skilleval.retention import prune_image, prune_images


def completed_run(root: Path, *, legacy: bool = False) -> tuple[Path, Path, Path]:
    source, digest = docker_archive(root / "images")
    output = root / "run"
    state = output / "runtime-state"
    state.mkdir(parents=True)
    shutil.copyfile(source, state / "image.tar")
    image = _image_archive(source, digest)
    record = {
        "schema_version": 1,
        "kind": "skilleval-lifecycle-recovery",
        "output": str(output),
        "runtime_state": str(state),
        "runtime_home": str(root / "removed-home"),
        "cleanup_complete": True,
        "runtime_home_removed": True,
        "create_attempted": True,
        "observer_positive": True,
        "observed_absent": True,
        "phase": "recovered",
        "image": image,
        "plan": {"image_archive": image},
        "cleanup": [{"action": action, "returncode": 0} for action in ("stop", "delete")],
    }
    evidence = output / ("report.json" if legacy else "recovery.json")
    evidence.write_text(json.dumps(record))
    (output / "result.json").write_text('{"finding":"retained"}')
    return output, source, evidence


@pytest.mark.parametrize("legacy", [False, True])
def test_preview_apply_and_repeat_preserve_source_and_original_evidence(
    tmp_path: Path, legacy: bool
) -> None:
    output, source, evidence = completed_run(tmp_path, legacy=legacy)
    original = {p: p.read_bytes() for p in (source, evidence, output / "result.json")}
    staged = output / "runtime-state/image.tar"
    allocated = staged.stat().st_blocks * 512
    preview = invoke("storage", "prune", str(output))
    assert preview.returncode == 0, preview.stderr
    assert json.loads(preview.stdout)["eligible_allocated_bytes"] == allocated > 0
    assert staged.is_file() and not (output / "image-retention.json").exists()
    applied = invoke("storage", "prune", str(output), "--apply")
    assert applied.returncode == 0, applied.stderr
    assert json.loads(applied.stdout)["removed_allocated_bytes"] == allocated
    assert not staged.exists()
    assert all(p.read_bytes() == data for p, data in original.items())
    receipt = (output / "image-retention.json").read_bytes()
    assert json.loads(receipt)["status"] == "removed"
    assert prune_image(output, apply=True)["status"] == "absent"
    assert (output / "image-retention.json").read_bytes() == receipt


@pytest.mark.parametrize(
    "fault",
    [
        "active",
        "home_present",
        "no_positive",
        "source_missing",
        "source_changed",
        "staged_changed",
        "staged_symlink",
        "source_symlink",
        "state_symlink",
        "bad_ticket",
        "ticket_symlink",
        "drift",
        "source_is_staging",
    ],
)
def test_unsafe_or_unresolved_copies_are_retained(tmp_path: Path, fault: str) -> None:
    output, source, evidence = completed_run(tmp_path)
    staged = output / "runtime-state/image.tar"
    record = json.loads(evidence.read_text())
    if fault == "active":
        record["cleanup_complete"] = False
    elif fault == "home_present":
        Path(record["runtime_home"]).mkdir()
    elif fault == "no_positive":
        record["observer_positive"] = False
    elif fault == "source_missing":
        source.unlink()
    elif fault == "source_changed":
        source.write_bytes(b"changed canonical image")
    elif fault == "staged_changed":
        staged.write_bytes(b"diagnostic modified copy")
    elif fault == "staged_symlink":
        staged.unlink()
        staged.symlink_to(source)
    elif fault == "source_symlink":
        moved = source.with_suffix(".saved")
        source.rename(moved)
        source.symlink_to(moved)
    elif fault == "state_symlink":
        moved = tmp_path / "other-state"
        staged.parent.rename(moved)
        staged.parent.symlink_to(moved, target_is_directory=True)
    elif fault == "drift":
        record["identity_changed"] = True
    elif fault == "source_is_staging":
        record["image"]["path"] = str(staged)
    evidence.write_text(json.dumps(record))
    if fault == "bad_ticket":
        evidence.write_text("invalid JSON")
    elif fault == "ticket_symlink":
        moved = evidence.with_suffix(".saved")
        evidence.rename(moved)
        evidence.symlink_to(moved)
    original = staged.read_bytes()
    result = prune_image(output, apply=True)
    assert result["status"] == "retained", result
    assert result["reason"]
    assert staged.read_bytes() == original


@pytest.mark.parametrize("fault", ["failed_delete", "cleanup_row", "plan"])
def test_legacy_refusal_does_not_abort_batch(tmp_path: Path, fault: str) -> None:
    output, _, evidence = completed_run(tmp_path / "bad", legacy=True)
    other, _, _ = completed_run(tmp_path / "good")
    record = json.loads(evidence.read_text())
    if fault == "failed_delete":
        record["cleanup"][-1]["returncode"] = 1
    elif fault == "cleanup_row":
        record["cleanup"] = [None]
    else:
        record["plan"] = None
    evidence.write_text(json.dumps(record))
    result = prune_images([output, other], apply=True)
    assert result["status"] == "partial"
    assert [r["status"] for r in result["runs"]] == ["retained", "removed"]
    assert (output / "runtime-state/image.tar").exists()
    assert not (other / "runtime-state/image.tar").exists()


def test_failed_unlink_retains_copy_and_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output, _, _ = completed_run(tmp_path)
    unlink = os.unlink

    def fail_image(name: str | Path, **kwargs: int) -> None:
        if name == "image.tar":
            raise PermissionError("injected deletion failure")
        unlink(name, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(os, "unlink", fail_image)
        assert prune_image(output, apply=True)["status"] == "retained"
    assert (output / "runtime-state/image.tar").is_file()
    assert prune_image(output, apply=True)["status"] == "removed"


def test_cli_refusal_is_nonzero_and_does_not_change_files(tmp_path: Path) -> None:
    output, source, _ = completed_run(tmp_path)
    source.unlink()
    result = invoke("storage", "prune", str(output), "--apply")
    assert result.returncode == 1
    assert json.loads(result.stdout)["status"] == "partial"
    assert (output / "runtime-state/image.tar").is_file()
