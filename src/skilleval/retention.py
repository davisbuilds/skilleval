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
from contextlib import suppress
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


_STAGING = ("checker-input.tar", "validation-input.tar", "tooling.tar")


def _staging_evidence(output: Path, name: str) -> dict[str, bytes | None]:
    """Bind only staging whose actual readers retain all required evidence.

    repair_assessment.checked_report reads repair-check.json/recovery.json;
    task_package.validated_package reads validation.json/recovery.json and the
    parent task-validation.json. Neither reopens its input tar. judge._run_files
    reads native-run/audit, final text and artifacts.tar, not runner tooling.
    In contrast admission._native opens qualification tooling and admission's
    artifact inventory rehashes it: any qualification report retains tooling.
    Producers are repair_check.check_repair, task_package.validate_environment,
    runner.run_native and scripts/qualify-native.py respectively. This allowlist must
    change if those evidence readers acquire another staging dependency.
    """
    _, recovery_name, raw = _completed(output)
    if recovery_name != "recovery.json":
        raise SkillEvalError("staging requires a completed recovery ticket")
    ticket = _parse_json(raw)
    if type(ticket.get("create_attempted")) is not bool:
        raise SkillEvalError("recovery create state is unknown")
    from skilleval.lifecycle import _environment, inventory_names

    if ticket.get("env") != _environment(output / "runtime-state", Path(ticket["runtime_home"])):
        raise SkillEvalError("recovery runtime namespace differs")
    if ticket["create_attempted"]:
        machine = ticket["machine_name"]
        if not isinstance(machine, str) or not machine:
            raise SkillEvalError("recovery machine is unknown")
        runtime = ticket["identity"]["runtime"]["binary"]
        if not isinstance(runtime, str) or not Path(runtime).is_absolute():
            raise SkillEvalError("recovery runtime is unknown")
        invocation = [runtime, "machine", "ls", "--json"]
        steps = ticket.get("steps")
        if not isinstance(steps, list) or any(not isinstance(step, dict) for step in steps):
            raise SkillEvalError("recovery inventory evidence is missing")
        inventories = [step for step in steps if step.get("argv") == invocation]
        positive = False
        for step in inventories:
            # Earlier failed observations remain diagnostic evidence.
            with suppress(SkillEvalError):
                if isinstance(step.get("stdout"), str):
                    positive |= machine in inventory_names(step)
        final = [step for step in steps if step.get("label") == "recovery_inventory_after"]
        if (
            not positive
            or not final
            or final[-1].get("argv") != invocation
            or final[-1] != inventories[-1]
            or not isinstance(final[-1].get("stdout"), str)
            or machine in inventory_names(final[-1])
        ):
            raise SkillEvalError("recovery lacks same-state positive and final absence evidence")
    snapshots: dict[str, bytes | None] = {recovery_name: raw}

    def read(path: Path) -> Any:
        with _directory(path.parent) as directory:
            content, _ = _read_file(directory, path.name)
        key = str(path.relative_to(output)) if path.is_relative_to(output) else "../" + path.name
        snapshots[key] = content
        return _parse_json(content)

    with _directory(output) as directory:
        try:
            os.stat("report.json", dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            snapshots["report.json"] = None
        else:
            if name == "tooling.tar":
                raise SkillEvalError("qualification tooling remains required by admission")

    if name == "checker-input.tar":
        report = read(output / "repair-check.json")
        if (
            not isinstance(report, dict)
            or report.get("schema_version") != 1
            or report.get("kind") != "skilleval-repair-check"
        ):
            raise SkillEvalError("unsupported checker staging producer")
    elif name == "validation-input.tar":
        report = read(output / "validation.json")
        parent = read(output.parent / "task-validation.json")
        if (
            not isinstance(report, dict)
            or report.get("case_id") != output.name
            or not isinstance(parent, dict)
            or parent.get("schema_version") != 1
            or parent.get("kind") != "skilleval-task-validation"
            or not isinstance(parent.get("cases"), list)
            or sum(row == report for row in parent["cases"]) != 1
        ):
            raise SkillEvalError("unsupported validation staging producer")
    else:
        report = read(output / "native-run.json")
        if (
            not isinstance(report, dict)
            or report.get("schema_version") != 1
            or report.get("kind")
            not in {"skilleval-native-run", "skilleval-native-context-control"}
            or report.get("recovery") != "recovery.json"
        ):
            raise SkillEvalError("unsupported native staging producer")
        if "audit" in report:
            # Native receipts compact their identity into a hash-bound full audit.
            from skilleval.judge import _native_audit

            audit = _native_audit(output, report)
            if audit is None:
                raise SkillEvalError("native staging audit is missing")
            snapshots["native-audit.json"] = audit
            report = _parse_json(audit)
    if (
        not isinstance(ticket.get("identity"), dict)
        or report.get("identity") != ticket["identity"]
        or report.get("image") != ticket["image"]
    ):
        raise SkillEvalError("staging producer and completed recovery differ")
    artifacts = report.get("artifacts", {})
    if not isinstance(artifacts, dict) or any(
        value in artifacts or value in artifacts.values()
        for value in (name, "runtime-state/" + name, str(output / "runtime-state" / name))
    ):
        raise SkillEvalError("staging is retained as an evidence artifact")
    return snapshots


def _staged_file(state: int, name: str) -> tuple[os.stat_result, str]:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=state)
    try:
        initial = os.fstat(descriptor)
        if not stat.S_ISREG(initial.st_mode):
            raise SkillEvalError("staging must be a regular file, not a link")
        digest = hashlib.sha256()
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
        if _fingerprint(initial) != _fingerprint(os.fstat(descriptor)) or _fingerprint(
            initial
        ) != _fingerprint(os.stat(name, dir_fd=state, follow_symlinks=False)):
            raise SkillEvalError("staging changed while reading")
        return initial, digest.hexdigest()
    finally:
        os.close(descriptor)


def _retention_reason(error: Exception) -> str:
    return str(error) if isinstance(error, SkillEvalError) else "cannot verify retention inputs"


def prune_staging(output: Path, *, apply: bool = False) -> dict[str, Any]:
    """Opt in to disposable input-tar cleanup for one explicitly selected run.

    Preview is read-only. Apply records its decisions separately, rechecks the
    completed recovery and producer evidence before each unlink, and never
    recurses, prunes by age, or removes image/artifact archives or unknown files.
    Recovered failures are eligible too; missing producer evidence is retained.
    Like image retention, this is an offline operator policy, not a concurrent
    controller supervisor or an independent machine inventory observation.
    """
    from skilleval.lifecycle import _write

    output = output.absolute()
    rows: list[dict[str, Any]] = []
    result: dict[str, Any] = {"output": str(output), "status": "absent", "files": rows}
    prepared: dict[str, tuple[os.stat_result, dict[str, bytes | None]]] = {}
    try:
        with _directory(output / "runtime-state") as state:
            state_identity = os.fstat(state)
            for name in _STAGING:
                row: dict[str, Any] = {"path": "runtime-state/" + name, "status": "absent"}
                rows.append(row)
                try:
                    os.stat(name, dir_fd=state, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                try:
                    initial, digest = _staged_file(state, name)
                    snapshots = _staging_evidence(output, name)
                    if name == "tooling.tar":
                        producer_raw = (
                            snapshots.get("native-audit.json") or snapshots["native-run.json"]
                        )
                        if producer_raw is None:
                            raise SkillEvalError("native staging producer is missing")
                        producer = _parse_json(producer_raw)
                        if producer.get("tooling_sha256") != digest:
                            raise SkillEvalError("native tooling identity differs")
                    prepared[name] = initial, snapshots
                    row.update(
                        status="eligible",
                        sha256=digest,
                        logical_bytes=initial.st_size,
                        allocated_bytes=initial.st_blocks * 512 if initial.st_nlink == 1 else 0,
                        evidence_sha256={
                            path: hashlib.sha256(raw).hexdigest()
                            for path, raw in snapshots.items()
                            if raw is not None
                        },
                    )
                except (OSError, SkillEvalError, KeyError, TypeError, ValueError) as error:
                    row.update(status="retained", reason=_retention_reason(error))
            if apply and any(row["status"] != "absent" for row in rows):
                _write(output / "staging-retention.json", {**result, "status": "removing"})
                for row in rows:
                    if row["status"] != "eligible":
                        continue
                    name = Path(row["path"]).name
                    initial, snapshots = prepared[name]
                    try:
                        with _directory(output / "runtime-state") as current:
                            current_identity = os.fstat(current)
                        observed, digest = _staged_file(state, name)
                        if (
                            (state_identity.st_dev, state_identity.st_ino)
                            != (current_identity.st_dev, current_identity.st_ino)
                            or _fingerprint(initial) != _fingerprint(observed)
                            or digest != row["sha256"]
                            or _staging_evidence(output, name) != snapshots
                        ):
                            raise SkillEvalError("retention inputs changed; staging retained")
                        os.unlink(name, dir_fd=state)
                        row["status"] = "removed"
                        os.fsync(state)
                    except (OSError, SkillEvalError, KeyError, TypeError, ValueError) as error:
                        if row["status"] != "removed":
                            row["status"] = "retained"
                        row["reason"] = _retention_reason(error)
    except (OSError, SkillEvalError, KeyError, TypeError, ValueError) as error:
        result["reason"] = _retention_reason(error)
        for row in rows:
            if row["status"] == "eligible":
                row.update(status="retained", reason=result["reason"])
    statuses = {row["status"] for row in rows} - {"absent"}
    result["status"] = (
        "partial"
        if len(statuses) > 1
        else next(iter(statuses), "retained" if result.get("reason") else "absent")
    )
    if apply and (statuses or result.get("reason")):
        try:
            with _directory(output):
                _write(output / "staging-retention.json", result)
        except OSError:
            result["receipt_error"] = "cannot persist retention result"
    return result


def prune_staging_runs(outputs: list[Path], *, apply: bool = False) -> dict[str, Any]:
    """Report explicit targets and all allowlisted files, including refusals."""
    runs = [prune_staging(output, apply=apply) for output in dict.fromkeys(outputs)]
    return {
        "schema_version": 1,
        "kind": "skilleval-staging-retention",
        "status": "partial"
        if any(
            run.get("reason")
            or run.get("receipt_error")
            or any(row.get("reason") for row in run["files"])
            for run in runs
        )
        else "complete",
        "apply": apply,
        "runs": runs,
        **{
            status + "_allocated_bytes": sum(
                row["allocated_bytes"]
                for run in runs
                for row in run["files"]
                if row["status"] == status
            )
            for status in ("eligible", "removed")
        },
    }
