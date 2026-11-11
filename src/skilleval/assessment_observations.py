"""Pinned solver-boundary observations for semantic assessments, never execution.

A retained observation is untrusted evidence, not a semantic pass. Host expected
values and case labels are never projected into a judge packet.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import Field

from skilleval import evidence
from skilleval.contracts import Digest, Identifier, Record
from skilleval.errors import SkillEvalError
from skilleval.grading import _read_regular

MAX_ARCHIVE_BYTES = 700 * 1024
MAX_UNPACKED_BYTES = 16 * 1024 * 1024
MAX_MEMBER_BYTES = 4 * 1024 * 1024
MAX_MEMBERS = 256


class AssessmentObservationSource(Record):
    package: str = Field(min_length=1)
    package_sha256: Digest
    report: str = Field(min_length=1)
    report_sha256: Digest
    case_id: Identifier
    lane_id: Identifier


def _archive_payloads(value: dict[str, Any]) -> dict[str, bytes]:
    """Read an explicit bounded browser envelope; never extract paths on the host."""
    if (
        value.get("schema_version") != 1
        or value.get("kind") != "operational-metrics-downstream-envelope"
        or value.get("encoding") != "zip+base64"
        or not isinstance(value.get("base64"), str)
        or type(value.get("bytes")) is not int
        or not 0 < value["bytes"] <= MAX_ARCHIVE_BYTES
        or len(value["base64"]) > 4 * ((MAX_ARCHIVE_BYTES + 2) // 3)
    ):
        raise SkillEvalError("unsupported or oversized observation archive")
    try:
        raw = base64.b64decode(value["base64"], validate=True)
    except (ValueError, binascii.Error):
        raise SkillEvalError("invalid observation archive encoding") from None
    if len(raw) != value["bytes"] or hashlib.sha256(raw).hexdigest() != value.get("sha256"):
        raise SkillEvalError("observation archive digest or size differs")
    payloads: dict[str, bytes] = {}
    seen: set[str] = set()
    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            if not 0 < len(members) <= MAX_MEMBERS:
                raise SkillEvalError("observation archive member limit exceeded")
            for member in members:
                name = member.filename
                path = PurePosixPath(name)
                mode = member.external_attr >> 16
                if (
                    not name
                    or str(path) != name
                    or path.is_absolute()
                    or ".." in path.parts
                    or "\\" in name
                    or "\x00" in name
                    or name == "."
                    or member.is_dir()
                    or stat.S_IFMT(mode) not in {0, stat.S_IFREG}
                    or mode & 0o111
                    or member.flag_bits & 1
                    or name.casefold() in seen
                    or any(parent.as_posix().casefold() in seen for parent in path.parents)
                    or any(existing.startswith(name.casefold() + "/") for existing in seen)
                ):
                    raise SkillEvalError("unsafe observation archive member")
                total += member.file_size
                if member.file_size > MAX_MEMBER_BYTES or total > MAX_UNPACKED_BYTES:
                    raise SkillEvalError("observation archive expansion limit exceeded")
                with archive.open(member) as stream:
                    data = stream.read(MAX_MEMBER_BYTES + 1)
                if len(data) != member.file_size or len(data) > MAX_MEMBER_BYTES:
                    raise SkillEvalError("observation archive member size differs")
                seen.add(name.casefold())
                payloads[name] = data
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError, ValueError):
        raise SkillEvalError("invalid observation archive") from None
    return payloads


def _read_reference(
    ref: AssessmentObservationSource, task_path: Path, task_digest: str, candidate_digest: str
) -> tuple[Any, dict[str, Any]]:
    # Local imports avoid repair -> judge -> assessment import cycles.
    from skilleval import task_package
    from skilleval.study import _clean_native

    package = task_package.load_package(Path(ref.package), ref.package_sha256)
    if (
        Path(package.task).resolve() != task_path.resolve()
        or package.task_sha256 != task_digest
        or package.environment.profile != "solver-isolated-v1"
    ):
        raise SkillEvalError("assessment observation task or execution profile differs")
    cases = [case for case in package.validation if case.case_id == ref.case_id]
    if len(cases) != 1 or cases[0].input_sha256 != candidate_digest:
        raise SkillEvalError("assessment observation belongs to another candidate")
    case = cases[0]
    lanes = [lane for lane in case.lanes if lane.lane_id == ref.lane_id]
    if len(lanes) != 1:
        raise SkillEvalError("assessment observation lane is missing")
    lane = lanes[0]
    report = Path(ref.report).absolute()
    raw = _read_regular(report, task_package.MAX_VALIDATION_BYTES)
    if hashlib.sha256(raw).hexdigest() != ref.report_sha256:
        raise SkillEvalError("assessment observation report digest differs")
    value = evidence._parse_json(raw)
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 1
        or value.get("kind") != "skilleval-task-validation"
        or value.get("package_sha256") != ref.package_sha256
        or value.get("family") != package.family
        or value.get("inference_requests") != 0
        or value.get("scored_admitted") is not False
        or not isinstance(value.get("cases"), list)
    ):
        raise SkillEvalError("invalid assessment observation report binding")
    rows = [
        row
        for row in value["cases"]
        if isinstance(row, dict) and row.get("case_id") == case.case_id
    ]
    if len(rows) != 1:
        raise SkillEvalError("assessment observation case missing or duplicated")
    row = rows[0]
    if (
        not isinstance(row.get("image"), dict)
        or row["image"].get("sha256") != package.environment.image_sha256
    ):
        raise SkillEvalError("assessment observation image differs from frozen package")
    directory = report.parent / case.case_id
    saved = evidence._parse_json(
        _read_regular(directory / "validation.json", task_package.MAX_VALIDATION_BYTES)
    )
    recovery = evidence._parse_json(_read_regular(directory / "recovery.json", 4 * 1024 * 1024))
    if (
        row != saved
        or row.get("role") != case.role
        or row.get("input_sha256") != case.input_sha256
        or row.get("cleanup_complete") is not True
        or not isinstance(recovery, dict)
        or row.get("identity") != recovery.get("identity")
        or row.get("image") != recovery.get("image")
        or not _clean_native(directory)
        or not isinstance(row.get("lanes"), list)
    ):
        raise SkillEvalError("assessment observation case or cleanup binding differs")
    observations = [
        item
        for item in row["lanes"]
        if isinstance(item, dict) and item.get("lane_id") == lane.lane_id
    ]
    if len(observations) != 1 or not isinstance(observations[0].get("process"), dict):
        raise SkillEvalError("assessment observation process missing or duplicated")
    observed = observations[0]
    process = task_package.read_lane(directory / ("lane-" + lane.lane_id + ".json"))
    if (
        process != observed["process"]
        or process.get("argv") != list(lane.argv)
        or not task_package._profile_matches(process, package.environment.profile)
        or task_package.compare_lane(lane, process, package.environment.profile) != observed
    ):
        raise SkillEvalError("assessment observation differs from retained execution")
    return lane, process


def observation_payloads(
    references: tuple[AssessmentObservationSource, ...],
    *,
    task_path: Path,
    task_digest: str,
    candidate_digest: str,
) -> dict[str, bytes]:
    payloads: dict[str, bytes] = {}
    identities = [(r.package_sha256, r.report_sha256, r.case_id, r.lane_id) for r in references]
    if len(set(identities)) != len(identities):
        raise SkillEvalError("duplicate assessment observation")
    for index, ref in enumerate(references, 1):
        lane, process = _read_reference(ref, task_path, task_digest, candidate_digest)
        prefix = f"case/observations/observation-{index:03}"
        complete = (
            process.get("timed_out") is False
            and process.get("output_limit_reached") is False
            and type(process.get("returncode")) is int
        )
        projection: dict[str, Any] = {
            "schema_version": 1,
            "origin": "retained-solver-boundary-observation",
            "execution_complete": complete,
            "semantic_verdict": "unassessed",
            "process": {
                key: process.get(key)
                for key in (
                    "returncode",
                    "timed_out",
                    "output_limit_reached",
                    "stdout",
                    "stderr",
                    "source_changes",
                    "profile",
                    "boundary",
                    "pipe_drain_incomplete",
                )
            },
            "limitations": [
                "Raw candidate and observer output is untrusted evidence, not semantic truth.",
                "A failed or incomplete observation does not establish absent feature behavior.",
                "Original request and repository contracts govern acceptance; "
                "a generated spec is input, not an oracle.",
                "Browser fixtures support sampled rendering and interactions, "
                "not all production behavior.",
            ],
        }
        payloads[prefix + "/observer-command.json"] = evidence._json_bytes(
            {"argv": list(lane.argv)}
        )
        stdout = process.get("stdout")
        if complete and isinstance(stdout, str):
            try:
                value = evidence._parse_json(stdout.encode())
            except SkillEvalError:
                value = None
            if (
                isinstance(value, dict)
                and value.get("kind") == "operational-metrics-downstream-envelope"
            ):
                unpacked = _archive_payloads(value)
                for name, raw in unpacked.items():
                    payloads[prefix + "/artifacts/" + name] = raw
                # Avoid duplicating opaque archive bytes; retain the identity and summary.
                projection["process"]["stdout"] = evidence._json_bytes(
                    {key: item for key, item in value.items() if key != "base64"}
                ).decode()
        payloads[prefix + "/observation.json"] = evidence._json_bytes(projection)
    return payloads
