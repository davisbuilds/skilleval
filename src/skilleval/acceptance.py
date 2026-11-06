"""Immutable operator acceptance designs; structure and hashes do not prove validity."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator

from skilleval import evidence, repair
from skilleval.contracts import Digest, Identifier, Record
from skilleval.errors import SkillEvalError
from skilleval.grading import _read_regular
from skilleval.repair_assessment import CRITERIA, DOCUMENT_CRITERIA
from skilleval.task_package import TaskPackageSpec, load_package

MAX_MAP_BYTES = 4 * 1024 * 1024
MAX_ATTACHMENT_BYTES = 16 * 1024 * 1024
Text = Annotated[str, Field(min_length=1, max_length=16384, pattern=r"\S")]
FilePath = Annotated[str, Field(min_length=1, max_length=4096, pattern=r"\S")]


class FileReference(Record):
    path: FilePath
    sha256: Digest


class MechanicalRoute(Record):
    probe_id: Identifier
    pointer: str = Field(max_length=4096)


class SemanticRoute(Record):
    criterion: str = Field(min_length=1, max_length=128)
    question: Text


class AcceptanceRequirement(Record):
    requirement_id: Identifier
    statement: Text
    role: Literal["core", "regression", "evidence"]
    criticality: Literal["required", "supporting"]
    source_rationale: Text
    mechanical: tuple[MechanicalRoute, ...] = Field(default=(), max_length=100)
    semantic: tuple[SemanticRoute, ...] = Field(default=(), max_length=16)
    gaps: tuple[Text, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def coherent(self) -> AcceptanceRequirement:
        if not (self.mechanical or self.semantic or self.gaps):
            raise ValueError("acceptance requirement needs an evidence route or explicit gap")
        routes = [(r.probe_id, r.pointer) for r in self.mechanical]
        semantics = [(r.criterion, r.question) for r in self.semantic]
        if len(set(routes)) != len(routes) or len(set(semantics)) != len(semantics):
            raise ValueError("duplicate acceptance evidence route")
        return self


class UnmappedProbe(Record):
    probe_id: Identifier
    reason: Text


class AcceptanceControl(Record):
    control_id: Identifier
    purpose: Text
    requirement_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=200)
    expected_discrimination: Text
    limitations: tuple[Text, ...] = Field(min_length=1, max_length=32)
    evidence: FileReference | None = None

    @model_validator(mode="after")
    def distinct_requirements(self) -> AcceptanceControl:
        if len(set(self.requirement_ids)) != len(self.requirement_ids):
            raise ValueError("duplicate control requirement ID")
        return self


class AcceptanceMapSpec(Record):
    map_id: Identifier
    task: FilePath
    task_sha256: Digest
    package: FilePath
    package_sha256: Digest
    purpose: Text
    population: Text
    selection_rationale: Text
    exposure_history: Text
    authored_after_outcomes: bool
    requirements: tuple[AcceptanceRequirement, ...] = Field(min_length=1, max_length=200)
    unmapped_probes: tuple[UnmappedProbe, ...] = Field(default=(), max_length=100)
    controls: tuple[AcceptanceControl, ...] = Field(default=(), max_length=100)
    review_evidence: tuple[FileReference, ...] = Field(default=(), max_length=32)
    open_questions: tuple[Text, ...] = Field(default=(), max_length=32)
    limitations: tuple[Text, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def coherent(self) -> AcceptanceMapSpec:
        requirement_ids = [r.requirement_id for r in self.requirements]
        control_ids = [c.control_id for c in self.controls]
        unmapped = [p.probe_id for p in self.unmapped_probes]
        for label, values in (
            ("requirement", requirement_ids),
            ("control", control_ids),
            ("unmapped probe", unmapped),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"duplicate acceptance {label} ID")
        for control in self.controls:
            if not set(control.requirement_ids).issubset(requirement_ids):
                raise ValueError("control references an unknown acceptance requirement")
        mapped = {r.probe_id for requirement in self.requirements for r in requirement.mechanical}
        if mapped.intersection(unmapped):
            raise ValueError("acceptance probe cannot be both mapped and unmapped")
        return self


def _select(value: Any, pointer: str) -> Any:
    """Resolve RFC 6901 tokens strictly against an already frozen JSON value."""
    if pointer == "":
        return value
    if not pointer.startswith("/"):
        raise SkillEvalError("acceptance pointer must be empty or start with /")
    for token in pointer[1:].split("/"):
        if re.search(r"~(?:[^01]|$)", token):
            raise SkillEvalError("invalid acceptance pointer escape")
        token = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", token):
            if len(token) > 20 or int(token) >= len(value):
                raise SkillEvalError("acceptance pointer array index is out of range")
            value = value[int(token)]
        else:
            raise SkillEvalError("acceptance pointer does not select a frozen expected field")
    return value


def _validate_routes(spec: AcceptanceMapSpec, task: repair.RepairSpec, family: str) -> None:
    """Validate declared traceability without evaluating an outcome."""
    probes = {probe.probe_id: probe for probe in task.probes}
    mapped: set[str] = set()
    criteria = DOCUMENT_CRITERIA if family == "document" else CRITERIA
    for requirement in spec.requirements:
        for route in requirement.mechanical:
            if route.probe_id not in probes:
                raise SkillEvalError("acceptance route references an unknown frozen probe")
            _select(probes[route.probe_id].expected, route.pointer)
            mapped.add(route.probe_id)
        for semantic in requirement.semantic:
            if semantic.criterion not in criteria:
                raise SkillEvalError("acceptance semantic criterion differs from package family")
    unmapped = {probe.probe_id for probe in spec.unmapped_probes}
    if unmapped - probes.keys():
        raise SkillEvalError("acceptance exclusion references an unknown frozen probe")
    if mapped | unmapped != probes.keys():
        raise SkillEvalError("every frozen probe needs an acceptance mapping or explicit exclusion")


def _outside(path: Path, roots: list[Path]) -> None:
    if any(path.resolve().is_relative_to(root.resolve()) for root in roots):
        raise SkillEvalError("acceptance artifacts must stay outside solver inputs")


def _inputs(spec: AcceptanceMapSpec, artifact: Path | None = None) -> TaskPackageSpec:
    package = load_package(Path(spec.package), spec.package_sha256)
    if package.task_sha256 != spec.task_sha256 or (
        Path(package.task).resolve() != Path(spec.task).resolve()
    ):
        raise SkillEvalError("acceptance map belongs to another package task")
    task = repair.load_task(Path(spec.task), spec.task_sha256)
    roots = [Path(task.input_bundle), *(Path(c.input_bundle) for c in package.validation)]
    if artifact is not None:
        _outside(artifact, roots)
    for ref in (
        *spec.review_evidence,
        *(control.evidence for control in spec.controls if control.evidence is not None),
    ):
        _outside(Path(ref.path), roots)
        if (
            hashlib.sha256(_read_regular(Path(ref.path), MAX_ATTACHMENT_BYTES)).hexdigest()
            != ref.sha256
        ):
            raise SkillEvalError("acceptance evidence digest differs")
    _validate_routes(spec, task, package.family)
    return package


def prepare_acceptance(spec: AcceptanceMapSpec, output: Path) -> dict[str, Any]:
    """Freeze a declaration offline without executing task, worker or evidence files."""
    data = spec.model_dump(mode="json")
    for key in ("task", "package"):
        data[key] = str(Path(data[key]).absolute())
    for ref in data["review_evidence"] + [
        c["evidence"] for c in data["controls"] if c["evidence"] is not None
    ]:
        ref["path"] = str(Path(ref["path"]).absolute())
    spec = AcceptanceMapSpec.model_validate_json(json.dumps(data))
    _inputs(spec, output)
    value = {"schema_version": 1, "kind": "skilleval-acceptance-map", "spec": data}
    if len(evidence._json_bytes(value)) > MAX_MAP_BYTES:
        raise SkillEvalError("acceptance map exceeds limit")
    digest = repair._publish(output, value)
    return {
        "status": "prepared",
        "path": str(output.absolute()),
        "sha256": digest,
        **summarize_acceptance(spec),
    }


def load_acceptance(path: Path, digest: str) -> AcceptanceMapSpec:
    raw = _read_regular(path, MAX_MAP_BYTES)
    if hashlib.sha256(raw).hexdigest() != digest:
        raise SkillEvalError("acceptance map digest differs")
    value = evidence._parse_json(raw)
    if (
        not isinstance(value, dict)
        or set(value) != {"schema_version", "kind", "spec"}
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
        or value["kind"] != "skilleval-acceptance-map"
    ):
        raise SkillEvalError("unsupported acceptance map")
    spec = AcceptanceMapSpec.model_validate_json(json.dumps(value["spec"]))
    _inputs(spec, path)
    return spec


def summarize_acceptance(spec: AcceptanceMapSpec) -> dict[str, Any]:
    """Summarize declared scope; attachment identity conveys no approval."""
    return {
        "map_id": spec.map_id,
        "task_sha256": spec.task_sha256,
        "package_sha256": spec.package_sha256,
        "population": spec.population,
        "authored_after_outcomes": spec.authored_after_outcomes,
        "requirement_count": len(spec.requirements),
        "requirements_with_gaps": [r.requirement_id for r in spec.requirements if r.gaps],
        "unmapped_probes": [p.probe_id for p in spec.unmapped_probes],
        "controls": [
            {
                "control_id": c.control_id,
                "status": "evidence-attached" if c.evidence is not None else "pending",
            }
            for c in spec.controls
        ],
        "review_evidence_count": len(spec.review_evidence),
        "review_state": "evidence-attached" if spec.review_evidence else "pending",
        "semantic_validity": "unassessed",
        "provenance": "declared-unverified",
        "scored_admitted": False,
    }


def render_acceptance(spec: AcceptanceMapSpec) -> str:
    """Render the operator's map without synthesizing requirement verdicts."""
    lines = [
        f"# Acceptance map: {spec.map_id}",
        "",
        "Declared traceability only. Semantic validity and provenance are unverified; "
        "evidence attachments do not establish independent audit or scored admission.",
        "",
        f"Purpose: {spec.purpose}",
        f"Population: {spec.population}",
        f"Selection rationale: {spec.selection_rationale}",
        f"Exposure history: {spec.exposure_history}",
        f"Authored after outcomes: {str(spec.authored_after_outcomes).lower()}",
        f"Task digest: {spec.task_sha256}",
        f"Package digest: {spec.package_sha256}",
    ]
    for requirement in spec.requirements:
        lines += [
            "",
            f"## {requirement.requirement_id}",
            "",
            requirement.statement,
            f"Role: {requirement.role}; criticality: {requirement.criticality}.",
            f"Source rationale: {requirement.source_rationale}",
            "",
        ]
        lines += [
            f"- Probe {r.probe_id}; JSON Pointer: {json.dumps(r.pointer)}."
            for r in requirement.mechanical
        ]
        lines += [f"- Semantic route {r.criterion}: {r.question}" for r in requirement.semantic]
        lines += [f"- Coverage gap: {gap}" for gap in requirement.gaps]
    lines += ["", "## Controls", ""]
    for control in spec.controls:
        status = "evidence-attached" if control.evidence is not None else "pending"
        lines += [
            f"- {control.control_id}: {status}; {control.purpose}",
            f"  Requirements: {', '.join(control.requirement_ids)}.",
            f"  Expected discrimination: {control.expected_discrimination}",
            f"  Limitations: {'; '.join(control.limitations)}",
        ]
        if control.evidence is not None:
            lines += [f"  Attachment: {control.evidence.path}; SHA256 {control.evidence.sha256}."]
    for title, items in (
        ("Unmapped probes", [f"{p.probe_id}: {p.reason}" for p in spec.unmapped_probes]),
        ("Review evidence", [f"{r.path}; SHA256 {r.sha256}" for r in spec.review_evidence]),
        ("Open questions", list(spec.open_questions)),
        ("Limitations", list(spec.limitations)),
    ):
        lines += ["", f"## {title}", ""]
        lines += [f"- {item}" for item in items] or ["None declared."]
    return "\n".join(lines) + "\n"


def write_acceptance_review(spec: AcceptanceMapSpec, destination: Path) -> str:
    """Publish a readable operator artifact outside every captured solver input."""
    _inputs(spec, destination)
    raw = render_acceptance(spec).encode()
    if len(raw) > MAX_MAP_BYTES:
        raise SkillEvalError("acceptance review exceeds limit")
    with evidence._directory(destination.parent) as directory:
        temporary = ".acceptance-" + uuid.uuid4().hex
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(
                temporary,
                destination.name,
                src_dir_fd=directory,
                dst_dir_fd=directory,
                follow_symlinks=False,
            )
            os.fsync(directory)
        finally:
            os.unlink(temporary, dir_fd=directory)
    return hashlib.sha256(raw).hexdigest()
