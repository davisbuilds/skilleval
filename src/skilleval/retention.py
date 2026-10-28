"""Release verified image staging copies after whole-machine cleanup.

The original image and all run evidence remain in place. This is a trusted local
operator policy, not VM recovery, a general cache collector, or a concurrent
controller supervisor. Historical tickets are read without upgrading their
runtime/source identities or rewriting their bytes.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Any

from skilleval.errors import SkillEvalError
from skilleval.evidence import _directory, _fingerprint, _parse_json, _read_file
from skilleval.qualification import _image_archive


def _completed(output: Path) -> tuple[dict[str, Any], str, bytes]:
    with _directory(output) as directory:
        # A malformed ticket must not fall back to a more permissive old report.
        try:
            os.stat("recovery.json", dir_fd=directory, follow_symlinks=False)
            name = "recovery.json"
        except FileNotFoundError:
            name = "report.json"
        raw, _ = _read_file(directory, name, 16 * 1024 * 1024)
    record = _parse_json(raw)
    if not isinstance(record, dict) or any(
        record.get(key) is not True for key in ("cleanup_complete", "runtime_home_removed")
    ):
        raise SkillEvalError("completed VM cleanup is required")
    if record.get("identity_changed"):
        raise SkillEvalError("identity drift requires inspection; staging retained")
    if name == "recovery.json":
        if (
            record.get("kind") != "skilleval-lifecycle-recovery"
            or record.get("schema_version") != 1
            or record.get("phase") != "recovered"
            or record.get("output") != str(output)
            or record.get("runtime_state") != str(output / "runtime-state")
        ):
            raise SkillEvalError("invalid completed recovery ticket")
        if record.get("create_attempted") is not False and any(
            record.get(key) is not True for key in ("observer_positive", "observed_absent")
        ):
            raise SkillEvalError("machine absence was not established")
        image = record["image"]
    else:
        # Older qualification runs predate recovery tickets. Require their
        # explicit successful stop/delete records, never infer from status alone.
        cleanup = record.get("cleanup", [])
        if not isinstance(cleanup, list) or any(not isinstance(row, dict) for row in cleanup):
            raise SkillEvalError("invalid legacy cleanup records")
        for action in ("stop", "delete"):
            if not any(
                row.get("action") == action
                and row.get("returncode") == 0
                and not row.get("timed_out")
                and not row.get("pipe_drain_incomplete")
                for row in cleanup
            ):
                raise SkillEvalError("legacy report lacks completed stop/delete")
        plan = record.get("plan", {})
        if not isinstance(plan, dict):
            raise SkillEvalError("invalid legacy task plan")
        image = record.get("image_archive") or plan.get("image_archive")
    home = Path(record["runtime_home"])
    if not home.is_absolute() or os.path.lexists(home):
        raise SkillEvalError("runtime HOME still exists or is invalid")
    if not isinstance(image, dict):
        raise SkillEvalError("no retained local image identity")
    return image, name, raw


def prune_image(output: Path, *, apply: bool = False) -> dict[str, Any]:
    """Inspect one explicit run, optionally unlink only runtime-state/image.tar.

    Refusals retain the copy and return a reason. A separate retention receipt
    records applied actions; the original recovery/report artifacts never change.
    """
    output = output.absolute()
    result: dict[str, Any] = {"output": str(output), "status": "retained"}
    removed = False
    try:
        with _directory(output / "runtime-state") as state:
            try:
                initial = os.stat("image.tar", dir_fd=state, follow_symlinks=False)
            except FileNotFoundError:
                return {**result, "status": "absent"}
            if not stat.S_ISREG(initial.st_mode):
                raise SkillEvalError("staged image must be a regular file, not a link")
            image, evidence_name, evidence_raw = _completed(output)
            source = Path(image["path"])
            if (
                not source.is_absolute()
                or source.is_relative_to(output)
                or (source.name == "image.tar" and source.parent.name == "runtime-state")
            ):
                raise SkillEvalError("retain a source image outside run staging")
            source_before = source.lstat()
            if _image_archive(source, image["sha256"]) != image:
                raise SkillEvalError("retained source image identity differs")
            staged = output / "runtime-state/image.tar"
            observed = _image_archive(staged, image["sha256"])
            if observed != {**image, "path": str(staged)}:
                raise SkillEvalError("staged image identity differs")
            result.update(
                status="eligible",
                image_sha256=image["sha256"],
                retained_source=str(source),
                logical_bytes=initial.st_size,
                allocated_bytes=initial.st_blocks * 512 if initial.st_nlink == 1 else 0,
                evidence=evidence_name,
                evidence_sha256=hashlib.sha256(evidence_raw).hexdigest(),
            )
            if apply:
                from skilleval.lifecycle import _write

                receipt = output / "image-retention.json"
                _write(receipt, {**result, "status": "removing"})
                # Recheck paths and recorded cleanup immediately before unlink.
                if (
                    _fingerprint(initial)
                    != _fingerprint(os.stat("image.tar", dir_fd=state, follow_symlinks=False))
                    or _fingerprint(source_before) != _fingerprint(source.lstat())
                    or _completed(output) != (image, evidence_name, evidence_raw)
                ):
                    raise SkillEvalError("retention inputs changed; staging retained")
                os.unlink("image.tar", dir_fd=state)
                removed = True
                os.fsync(state)
                result["status"] = "removed"
                _write(receipt, result)
    except (OSError, SkillEvalError, KeyError, TypeError, ValueError) as error:
        result["status"] = "removed" if removed else "retained"
        result["reason"] = (
            str(error) if isinstance(error, SkillEvalError) else "cannot verify retention inputs"
        )
        if apply:
            from skilleval.lifecycle import _write

            try:
                with _directory(output):
                    _write(output / "image-retention.json", result)
            except OSError:
                result["receipt_error"] = "cannot persist retention result"
    return result


def prune_images(outputs: list[Path], *, apply: bool = False) -> dict[str, Any]:
    """Report every explicit target, including refusals and partial application."""
    runs = [prune_image(output, apply=apply) for output in dict.fromkeys(outputs)]
    return {
        "schema_version": 1,
        "kind": "skilleval-image-retention",
        "status": "partial" if any(row.get("reason") for row in runs) else "complete",
        "apply": apply,
        "runs": runs,
        "eligible_allocated_bytes": sum(
            row["allocated_bytes"] for row in runs if row["status"] == "eligible"
        ),
        "removed_allocated_bytes": sum(
            row["allocated_bytes"] for row in runs if row["status"] == "removed"
        ),
    }
