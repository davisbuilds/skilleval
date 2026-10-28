"""Offline preparation and identity-bound native judge stability comparisons.

No provider calls or imported-source execution. Stability is not accuracy;
all independently unaudited results remain development observations.
"""

import hashlib
import shutil
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator

from skilleval import calibration, evidence, grading, identity, judge
from skilleval.contracts import Digest, Identifier, Record
from skilleval.errors import SkillEvalError

VERSION = "judge-reliability-v1"
Family = Literal["equivalent-paraphrase", "anchoring", "embedded-prompt-injection", "treatment-cue"]
SLOTS = {"repeat-a": "original", "repeat-b": "original", "order-reversed": "reversed"}


class ReliabilityPair(Record):
    pair_id: Identifier
    family: Family
    left_probe_id: Identifier
    right_probe_id: Identifier

    @model_validator(mode="after")
    def different_probes(self) -> "ReliabilityPair":
        if self.left_probe_id == self.right_probe_id:
            raise ValueError("invariance pairs require distinct probes")
        return self


class ReliabilitySuite(Record):
    suite_id: Identifier
    reliability_version: Literal["judge-reliability-v1"]
    pairs: tuple[ReliabilityPair, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_pairs(self) -> "ReliabilitySuite":
        if len({pair.pair_id for pair in self.pairs}) != len(self.pairs):
            raise ValueError("pair IDs must be unique")
        return self


class ReliabilityObservation(Record):
    attempt_id: Identifier
    slot_id: Literal["repeat-a", "repeat-b", "order-reversed"]
    record_dir: str | None = Field(default=None, min_length=1)
    declared_failure: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def one_evidence_route(self) -> "ReliabilityObservation":
        if (self.record_dir is None) == (self.declared_failure is None):
            raise ValueError("supply one native record directory or declared failure")
        return self


class ReliabilityObservations(Record):
    plan_sha256: Digest
    attempts: tuple[ReliabilityObservation, ...] = ()

    @model_validator(mode="after")
    def unique_attempts(self) -> "ReliabilityObservations":
        if len({item.attempt_id for item in self.attempts}) != len(self.attempts):
            raise ValueError("attempt IDs must be unique")
        return self


def _read_model(path: Path, model: type[Record]) -> Any:
    raw = grading._read_regular(path, grading.MAX_ASSESSMENT_BYTES)
    grading._parse_json(raw)
    try:
        return model.model_validate_json(raw)
    except ValidationError:
        raise SkillEvalError(f"invalid {model.__name__} structure") from None


def _suite(bundle: Path) -> tuple[ReliabilitySuite, dict[str, Any], str, dict[str, bytes]]:
    suite, manifest, digest, _, _ = calibration._packet(bundle)
    payloads = {
        item["path"]: grading._read_payload(bundle, manifest, item["path"])
        for item in manifest["files"]
    }
    try:
        grading._parse_json(payloads["reliability.json"])
        controls = ReliabilitySuite.model_validate_json(payloads["reliability.json"])
    except (KeyError, ValidationError):
        raise SkillEvalError("invalid reliability suite metadata") from None
    if controls.suite_id != suite.suite_id:
        raise SkillEvalError("reliability and calibration suite identity mismatch")
    probes = {probe.probe_id: probe for probe in suite.probes}
    for pair in controls.pairs:
        left, right = probes.get(pair.left_probe_id), probes.get(pair.right_probe_id)
        if (
            left is None
            or right is None
            or left.kind != right.kind
            or left.expected != right.expected
        ):
            raise SkillEvalError("reliability pairs require compatible authored probe labels")
    payloads["manifest.json"] = grading._read_regular(
        bundle / "manifest.json", evidence.MAX_MANIFEST_BYTES
    )
    return controls, manifest, digest, payloads


def _reversed(payloads: dict[str, bytes], manifest: dict[str, Any]) -> dict[str, bytes]:
    output = dict(payloads)
    document = grading._parse_json(output["suite.json"])
    document["probes"].reverse()
    output["suite.json"] = grading._json_bytes(document)
    new_manifest = {**manifest, "files": [dict(item) for item in manifest["files"]]}
    for item in new_manifest["files"]:
        if item["path"] == "suite.json":
            item.update(size=len(output["suite.json"]), sha256=_sha(output["suite.json"]))
    output["manifest.json"] = grading._json_bytes(new_manifest)
    return output


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def prepare_reliability(
    bundle: Path, destination: Path, *, investigation: bool = True
) -> dict[str, Any]:
    """Prepare three prespecified session slots, reusing one identical repeat packet."""
    grading._outside(bundle, destination)
    controls, manifest, digest, payloads = _suite(bundle)
    destination = destination.absolute()
    created = False
    preparations: dict[str, Any] = {}
    try:
        destination.mkdir(mode=0o700, parents=True, exist_ok=False)
        created = True
        modes = {item["path"]: item["executable"] for item in manifest["files"]}
        for order, data in (("original", payloads), ("reversed", _reversed(payloads, manifest))):
            captured = destination / "evidence" / order
            judge._write_tree(captured, data, source_modes=modes)
            preparations[order] = judge.prepare_agentic_calibration(
                captured, destination / "packets" / order, investigation=investigation
            )
        plan = {
            "schema_version": 1,
            "kind": "skilleval-judge-reliability-plan",
            "reliability_version": VERSION,
            "suite_id": controls.suite_id,
            "source_evidence_sha256": digest,
            "investigation": investigation,
            "slots": SLOTS,
            "preparations": preparations,
        }
        raw = grading._json_bytes(plan)
        grading._private_write(destination / "reliability-plan.json", raw)
        grading._private_write(
            destination / "observations.template.json",
            grading._json_bytes({"schema_version": 1, "plan_sha256": _sha(raw), "attempts": []}),
        )
    except (OSError, SkillEvalError):
        if created:
            shutil.rmtree(destination)
        raise SkillEvalError("cannot create fresh reliability preparation") from None
    return {
        "status": "prepared",
        "path": str(destination),
        "plan_sha256": _sha(raw),
        "suite_id": controls.suite_id,
        "reliability_version": VERSION,
        "session_count": len(SLOTS),
        "pair_count": len(controls.pairs),
        "preparations": preparations,
        "slots": SLOTS,
        "independent_audit": "required-pending",
    }


def _prepared(prepared: Path) -> tuple[ReliabilitySuite, dict[str, Any], str, dict[str, Any]]:
    raw = grading._read_regular(prepared / "reliability-plan.json", grading.MAX_ASSESSMENT_BYTES)
    plan = grading._parse_json(raw)
    if (
        not isinstance(plan, dict)
        or plan.get("schema_version") != 1
        or plan.get("kind") != "skilleval-judge-reliability-plan"
        or plan.get("reliability_version") != VERSION
        or plan.get("slots") != SLOTS
        or type(plan.get("investigation")) is not bool
        or not isinstance(plan.get("preparations"), dict)
    ):
        raise SkillEvalError("invalid reliability preparation plan")
    controls, manifest, digest, payloads = _suite(prepared / "evidence/original")
    if plan.get("suite_id") != controls.suite_id or plan.get("source_evidence_sha256") != digest:
        raise SkillEvalError("reliability plan original evidence identity mismatch")
    expected_reversed = _reversed(payloads, manifest)
    reverse_manifest, _ = grading._verified(prepared / "evidence/reversed")
    for name, data in expected_reversed.items():
        actual = (
            grading._read_regular(prepared / "evidence/reversed" / name, len(data) + 1)
            if name == "manifest.json"
            else grading._read_payload(prepared / "evidence/reversed", reverse_manifest, name)
        )
        if actual != data:
            raise SkillEvalError("reliability reversed evidence differs from prespecified order")
    contexts = {}
    for order in ("original", "reversed"):
        context = judge._verified_packet(
            prepared / "evidence" / order, prepared / "packets" / order
        )
        meta = grading._parse_json(context[2]["packet.json"])
        if meta["investigation"] != plan["investigation"]:
            raise SkillEvalError("reliability investigation policy mismatch")
        receipt = plan.get("preparations", {}).get(order, {})
        if (
            not isinstance(receipt, dict)
            or receipt.get("packet_sha256") != context[3]
            or receipt.get("evidence_sha256") != context[1]
            or receipt.get("input_digest") != judge._input_digest(context[2])
            or receipt.get("prompt") != judge._invocation_prompt(context[2], context[3]).decode()
        ):
            raise SkillEvalError("reliability preparation receipt identity mismatch")
        contexts[order] = context
    return controls, plan, _sha(raw), contexts


class _ImageIdentity(Record):
    path: str = Field(min_length=1)
    sha256: Digest
    size: int = Field(gt=0)
    config_sha256: Digest
    layer_count: int = Field(ge=0)


class _CodexIdentity(Record):
    observed_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    baseline_version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    version_policy: str = Field(min_length=1)
    baseline_match: bool


class _GatewayLimits(Record):
    max_requests: int = Field(gt=0)
    max_body_bytes: int = Field(gt=0)
    timeout_seconds: float = Field(gt=0)
    max_concurrent_requests: int = Field(gt=0)
    lifetime_seconds: float = Field(gt=0)


class _ArtifactLimits(Record):
    bytes: int = Field(gt=0)
    files: int = Field(gt=0)


class _NativeExecution(Record):
    image: _ImageIdentity
    codex_identity: _CodexIdentity
    catalog_sha256: Digest
    native_timeout_seconds: int = Field(gt=0)
    gateway_limits: _GatewayLimits
    artifact_limits: _ArtifactLimits


def _execution_inputs(receipt: dict[str, Any], retained: dict[str, bytes]) -> dict[str, Any]:
    fields = set(_NativeExecution.model_fields) - {"schema_version"}
    compact = isinstance(receipt.get("identity"), dict) and "audit_field" in receipt["identity"]
    if compact:
        audit_raw = retained.get("native-audit.json")
        if "audit" not in receipt or audit_raw is None:
            raise SkillEvalError("compact native identity requires a retained hash-bound audit")
        audit = grading._parse_json(audit_raw)
        if not isinstance(audit, dict) or any(audit.get(key) != receipt.get(key) for key in fields):
            raise SkillEvalError("native execution inputs differ from retained audit")
    try:
        actual = _NativeExecution.model_validate({key: receipt[key] for key in fields})
    except (KeyError, ValidationError):
        raise SkillEvalError(
            "native execution identity or limits unavailable or malformed"
        ) from None
    # Image location is an evidence handle; content identity determines compatibility.
    return actual.model_dump(exclude={"schema_version": True, "image": {"path", "schema_version"}})


def _native_record(
    record: Path, context: Any, investigation: bool
) -> tuple[judge.AgenticCalibrationResult, dict[str, Any], str, str]:
    receipt, retained, artifacts = judge._run_files(record)
    judge._check_packet(record / "packet", context[2])
    judge._native_policy(receipt, retained, context[2], context[3], investigation)
    result = judge._read_result(retained["final.txt"])
    judge._judge_identity(result, receipt)
    judge._validate_judgments(result, context, artifacts)
    stored = judge._read_result(
        grading._read_regular(record / "result.json", grading.MAX_ASSESSMENT_BYTES)
    )
    if stored != result:
        raise SkillEvalError("retained result differs from native final artifact")
    execution_inputs = _execution_inputs(receipt, retained)
    runtime_identity = receipt.get("identity")
    if isinstance(runtime_identity, dict) and "audit_field" in runtime_identity:
        runtime_identity = grading._parse_json(retained["native-audit.json"]).get("identity")
    if (
        not isinstance(runtime_identity, dict)
        or not isinstance(runtime_identity.get("runtime"), dict)
        or not runtime_identity["runtime"]
        or not isinstance(runtime_identity.get("sources"), dict)
        or not runtime_identity["sources"]
    ):
        raise SkillEvalError("native runtime and source identity unavailable for compatibility")
    compatibility = identity.object_digest(
        {
            "model": receipt["model"],
            "effort": receipt["effort"],
            "investigation": investigation,
            "rubric_version": result.rubric_version,
            "identity": runtime_identity,
            "execution_inputs": execution_inputs,
        }
    )
    return result, receipt, compatibility, _sha(retained["native-run.json"])


def _counts(
    pairs: list[tuple[str | None, str | None]], *, comparable: bool = True
) -> dict[str, Any]:
    resolved = [
        (left, right)
        for left, right in pairs
        if left is not None and right is not None and left != "unresolved" and right != "unresolved"
    ]
    return {
        "opportunity_count": len(pairs),
        "observed_pair_count": sum(left is not None and right is not None for left, right in pairs),
        "observed_resolved_pair_count": len(resolved),
        "resolved_pair_count": len(resolved) if comparable else 0,
        "stable_resolved_count": sum(left == right for left, right in resolved)
        if comparable
        else 0,
        "changed_resolved_count": sum(left != right for left, right in resolved)
        if comparable
        else 0,
        "incompatible_pair_count": (
            sum(left is not None and right is not None for left, right in pairs)
            if not comparable
            else 0
        ),
        "both_unresolved_count": sum(left == right == "unresolved" for left, right in pairs),
        "one_unresolved_count": sum(
            (left == "unresolved") != (right == "unresolved")
            for left, right in pairs
            if left is not None and right is not None
        ),
        "missing_count": sum(left is None or right is None for left, right in pairs),
        "left_missing_count": sum(left is None for left, _ in pairs),
        "right_missing_count": sum(right is None for _, right in pairs),
    }


def compare_reliability(prepared: Path, observations_file: Path) -> dict[str, Any]:
    """Compare every explicit attempt; never select a retry or pool judge coverage."""
    controls, plan, plan_digest, contexts = _prepared(prepared)
    observations: ReliabilityObservations = _read_model(observations_file, ReliabilityObservations)
    if observations.plan_sha256 != plan_digest:
        raise SkillEvalError("reliability observations preparation identity mismatch")
    attempts: list[dict[str, Any]] = []
    results: dict[str, judge.AgenticCalibrationResult] = {}
    seen_runs: set[str] = set()
    seen_results: set[str] = set()
    for item in observations.attempts:
        row: dict[str, Any] = {"attempt_id": item.attempt_id, "slot_id": item.slot_id}
        if item.declared_failure is not None:
            row.update(status="declared-failed", reason=item.declared_failure)
        else:
            record = Path(item.record_dir or "")
            if not record.is_absolute():
                record = observations_file.parent / record
            row["record_dir"] = str(record.absolute())
            try:
                context = contexts[SLOTS[item.slot_id]]
                result, receipt, compatibility_digest, run_digest = _native_record(
                    record, context, plan["investigation"]
                )
                if run_digest in seen_runs or result.result_id in seen_results:
                    raise SkillEvalError("native run or result reused across reliability attempts")
                seen_runs.add(run_digest)
                seen_results.add(result.result_id)
                row.update(
                    status=receipt["status"],
                    result_id=result.result_id,
                    native_run_sha256=run_digest,
                    compatibility_sha256=compatibility_digest,
                    authored_label_comparison=judge._comparison(context[0], result),
                )
                results[item.attempt_id] = result
            except SkillEvalError as error:
                row.update(status="invalid", reason=str(error))
        attempts.append(row)
    invariant = []
    missing_slots = sorted(set(SLOTS) - {row["slot_id"] for row in attempts})
    invariant_rows = attempts + [
        {"attempt_id": None, "slot_id": slot, "status": "missing"} for slot in missing_slots
    ]
    for row in invariant_rows:
        invariant_result = results.get(row["attempt_id"]) if row["status"] == "completed" else None
        observed = (
            {item.probe_id: item.verdict for item in invariant_result.judgments}
            if invariant_result
            else {}
        )
        for family in sorted({pair.family for pair in controls.pairs}):
            invariant.append(
                {
                    "attempt_id": row["attempt_id"],
                    "family": family,
                    "slot_id": row["slot_id"],
                    "execution_status": row["status"],
                    "probe_pairs": [
                        {
                            "pair_id": pair.pair_id,
                            "left_probe_id": pair.left_probe_id,
                            "right_probe_id": pair.right_probe_id,
                            "left_verdict": observed.get(pair.left_probe_id),
                            "right_verdict": observed.get(pair.right_probe_id),
                        }
                        for pair in controls.pairs
                        if pair.family == family
                    ],
                    **_counts(
                        [
                            (observed.get(pair.left_probe_id), observed.get(pair.right_probe_id))
                            for pair in controls.pairs
                            if pair.family == family
                        ]
                    ),
                }
            )
    cross_session = []
    probe_ids = [probe.probe_id for probe in contexts["original"][0].probes]
    for cross_family, left_slot, right_slot in (
        ("repeat", "repeat-a", "repeat-b"),
        ("probe-order", "repeat-a", "order-reversed"),
    ):
        left_rows = [row for row in attempts if row["slot_id"] == left_slot]
        right_rows = [row for row in attempts if row["slot_id"] == right_slot]
        left_options: list[dict[str, Any] | None] = list(left_rows) or [None]
        right_options: list[dict[str, Any] | None] = list(right_rows) or [None]
        for left in left_options:
            for right in right_options:
                compatible = bool(
                    left
                    and right
                    and left["status"] == right["status"] == "completed"
                    and left["compatibility_sha256"] == right["compatibility_sha256"]
                )
                values = []
                for cross_row in (left, right):
                    cross_result = (
                        results.get(cross_row["attempt_id"])
                        if cross_row and cross_row["status"] == "completed"
                        else None
                    )
                    values.append(
                        {item.probe_id: item.verdict for item in cross_result.judgments}
                        if cross_result
                        else {}
                    )
                cross_session.append(
                    {
                        "family": cross_family,
                        "left_attempt_id": left["attempt_id"] if left else None,
                        "right_attempt_id": right["attempt_id"] if right else None,
                        "compatible_completed_pair": compatible,
                        "unavailable_reason": (
                            None
                            if compatible
                            else "missing-session"
                            if left is None or right is None
                            else "incomplete-or-invalid-execution"
                            if left["status"] != "completed" or right["status"] != "completed"
                            else "incompatible-native-identity"
                        ),
                        "probe_pairs": [
                            {
                                "probe_id": pid,
                                "left_verdict": values[0].get(pid),
                                "right_verdict": values[1].get(pid),
                            }
                            for pid in probe_ids
                        ],
                        **_counts(
                            [(values[0].get(pid), values[1].get(pid)) for pid in probe_ids],
                            comparable=compatible,
                        ),
                    }
                )
    return {
        "schema_version": 1,
        "reliability_version": VERSION,
        "suite_id": controls.suite_id,
        "plan_sha256": plan_digest,
        "expected_session_count": len(SLOTS),
        "attempt_count": len(attempts),
        "completed_count": sum(row["status"] == "completed" for row in attempts),
        "failed_count": sum(
            row["status"] in {"failed", "partial", "timed-out", "declared-failed"}
            for row in attempts
        ),
        "invalid_count": sum(row["status"] == "invalid" for row in attempts),
        "missing_slot_ids": missing_slots,
        "attempts": attempts,
        "invariance": invariant,
        "cross_session": cross_session,
        "independent_audit": "required-pending",
        "limitations": [
            "Resolved verdict stability is not accuracy; a judge can be consistently wrong.",
            "Authored-label agreement is separate and is not independent semantic audit.",
            "Public exposed development controls are not a holdout or a skill-effect estimate.",
            "Order effects cover probe order, not reordered findings or duplicate semantics.",
            "All supplied retries remain separate; comparison never selects the best attempt.",
            "Failure declarations are operator reports, not verified native executions.",
            "Packet invariance does not prove general injection resistance or blinding.",
        ],
    }
