"""Bounded request admission and evidence, using synthetic local transports only."""

import json
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from test_gateway import BODY, Stream, request
from test_gateway import short_root as short_root
from test_runner import invoke
from test_runner import route as route
from test_study import prepared as prepared

from skilleval import gateway, runner, study
from skilleval.errors import SkillEvalError


def test_default_backstop_admits_1024_then_vendor_429_preserves_all_observations(short_root):
    sent, observed = [], []

    def transport(raw, *_args):
        sent.append(raw)
        return Stream()

    auth = gateway.HostAuth("synthetic", "synthetic", time.time() + 600)
    with gateway.broker_scope(
        auth,
        short_root / "budget",
        limits=gateway.native_limits(30),
        test_transport=transport,
        request_observer=observed.append,
    ) as broker:
        for _ in range(1024):
            assert request(broker.socket_path)[0] == 200
        status, raw = request(broker.socket_path)
        assert status == 429
        assert json.loads(raw) == {"error": {"code": "request_limit"}}
    assert broker.clean is True
    assert len(sent) == 1024
    assert len(observed) == 1025
    assert len([row for row in broker.receipts if row.get("event") == "request"]) == 1024
    assert any(row.get("code") == "request_limit" for row in broker.receipts)
    assert not any(row.get("event") == "request_observation_failed" for row in broker.receipts)


def test_observation_capacity_refusal_is_explicit_latched_and_never_dispatches(short_root):
    calls, sent = [], []

    def observe(raw):
        calls.append(raw)
        raise gateway.RequestObservationLimitError("authored observation capacity")

    auth = gateway.HostAuth("synthetic", "synthetic", time.time() + 600)
    with gateway.broker_scope(
        auth,
        short_root / "observer",
        test_transport=lambda raw, *_: sent.append(raw) or Stream(),
        request_observer=observe,
    ) as broker:
        for _ in range(2):
            status, raw = request(broker.socket_path)
            assert status == 503
            assert json.loads(raw) == {"error": {"code": "request_observation_limit"}}
    assert len(calls) == 1 and not sent
    assert broker.clean is True
    assert sum(row.get("event") == "request_observation_limit" for row in broker.receipts) == 1


@pytest.mark.parametrize("limit", [0, 1025, True])
def test_admission_limit_above_1024_and_noninteger_values_are_refused(limit):
    with pytest.raises(SkillEvalError):
        gateway.GatewayLimits(max_requests=limit)


def test_native_and_new_study_backstops_are_explicit_and_bounded(prepared, tmp_path):
    assert gateway.GatewayLimits().max_requests == 4
    assert runner.DEFAULT_LIMITS.max_requests == 1024
    frozen = json.loads(prepared["plan_path"].read_text())
    original = study.StudySpec.model_validate_json(json.dumps(frozen["spec"]))
    assert original.max_requests_per_attempt == 1024
    assert original.model_dump(mode="json") == frozen["spec"]
    enlarged = study.StudySpec.model_validate_json(
        json.dumps({**frozen["spec"], "max_requests_per_attempt": 1024})
    )
    result = study.prepare_study(enlarged, tmp_path / "larger-plan.json")
    assert result["maximum_provider_requests"] == 2 * 2 * 1024
    for invalid in (0, 1025, True):
        with pytest.raises(ValidationError):
            study.StudySpec.model_validate_json(
                json.dumps({**frozen["spec"], "max_requests_per_attempt": invalid})
            )


@pytest.mark.parametrize("exhaust_observer", [False, True])
def test_native_request_budget_provenance_distinguishes_provider_and_observer_stops(
    tmp_path, route, monkeypatch, exhaust_observer
):
    count = gateway.MAX_REQUEST_OBSERVATIONS if exhaust_observer else 1025
    body = json.dumps(BODY).encode()

    @contextmanager
    def broker(_auth, directory, **kwargs):
        directory.mkdir(mode=0o700)
        try:
            for _ in range(count):
                kwargs["request_observer"](body)
            receipts = [
                {"event": "request", "outcome": "complete", "upstream_status": 200}
                for _ in range(1024)
            ]
            if exhaust_observer:
                with pytest.raises(gateway.RequestObservationLimitError):
                    kwargs["request_observer"](body)
                receipts.extend(
                    [
                        {"event": "request_observation_limit", "outcome": "refused"},
                        {"event": "rejected", "status": 503, "code": "request_observation_limit"},
                    ]
                )
            else:
                receipts.append({"event": "rejected", "status": 429, "code": "request_limit"})
            yield SimpleNamespace(
                socket_path=directory / "inference.sock", clean=True, receipts=receipts
            )
        finally:
            directory.rmdir()

    monkeypatch.setattr(runner, "broker_scope", broker)
    route["limits"] = gateway.GatewayLimits(
        max_requests=1024, timeout_seconds=90, lifetime_seconds=2820
    )
    report = invoke(tmp_path, route)
    assert report["request_count"] == 1024
    assert len(report["request_observations"]) == count
    assert [row["index"] for row in report["request_observations"]] == list(range(1, count + 1))
    budget = report["request_budget"]
    assert budget == {
        "admitted_limit": 1024,
        "admitted": 1024,
        "request_limit_rejections": int(not exhaust_observer),
        "observation_limit": gateway.MAX_RECEIPTS - 4,
        "observations": count,
        "observation_limit_rejections": int(exhaust_observer),
        "observation_failure": "request_observation_limit" if exhaust_observer else None,
    }
    assert study._summary_fields(report)["request_budget"] == budget
    if exhaust_observer:
        assert report["status"] != "completed"
    else:
        assert report["status"] == "completed"
    legacy = {**report}
    del legacy["request_budget"]
    assert "request_budget" not in study._summary_fields(legacy)


@pytest.mark.parametrize("copy_fails", [False, True])
def test_collection_failure_keeps_primary_diagnostic_and_unvalidated_raw_archive(
    tmp_path, route, monkeypatch, copy_fails
):
    from skilleval.lifecycle import Lifecycle

    original_guest = Lifecycle.guest

    def failed_collection(session, label, *args, **kwargs):
        response = original_guest(session, label, *args, **kwargs)
        if label == "collection_status":
            response["stdout"] = json.dumps(
                {"status": "failed", "error_type": "ValueError", "error": "primary archive fault"}
            )
        return response

    monkeypatch.setattr(Lifecycle, "guest", failed_collection)
    if copy_fails:

        def fail_copy(*args):
            raise SkillEvalError("secondary copy fault")

        monkeypatch.setattr(runner, "_copy_artifacts", fail_copy)

    def never_validate(*args):
        raise AssertionError("failed collection must not be treated as a complete artifact")

    monkeypatch.setattr(runner, "validate_artifacts", never_validate)
    report = invoke(tmp_path, route)
    assert report["status"] == "partial"
    assert "primary archive fault" in report["error"]
    assert report["collection_result"]["error_type"] == "ValueError"
    assert "artifacts" not in report
    assert (tmp_path / "run/artifacts.tar").is_file() is not copy_fails
    assert not (tmp_path / "run/artifacts.json").exists()
    if copy_fails:
        assert report["failed_artifact_copy_error"] == "SkillEvalError"


@pytest.mark.parametrize("schema", [1, 2, 3, 4])
@pytest.mark.parametrize("recorded", [None, 7, 32, 128])
def test_legacy_frozen_request_limits_and_bytes_survive(prepared, schema, recorded):
    import hashlib

    path = prepared["plan_path"]
    plan = json.loads(path.read_bytes())
    plan["schema_version"] = schema
    if schema < 4:
        plan.pop("acceptance_policy")
    if schema < 3:
        for task in plan["spec"]["tasks"]:
            task.pop("mode")
    if schema == 1:
        for key in ("invocation_policy", "price_schedule"):
            plan["spec"].pop(key)
        for key in ("invocations", "skill_name"):
            plan.pop(key)
    if recorded is None:
        plan["spec"].pop("max_requests_per_attempt")
    else:
        plan["spec"]["max_requests_per_attempt"] = recorded
    raw = json.dumps(plan).encode()
    path.write_bytes(raw)
    value, spec = study._plan(path, hashlib.sha256(raw).hexdigest())
    assert spec.max_requests_per_attempt == (32 if recorded is None else recorded)
    assert value == plan and path.read_bytes() == raw


def test_full_backstop_with_long_context_keeps_readable_receipt_and_full_exposure_audit(
    tmp_path, route, prepared, monkeypatch
):
    import hashlib
    from pathlib import Path

    from skilleval.judge import _run_files

    spec = json.loads(prepared["plan_path"].read_bytes())["spec"]
    route.update(skill_bundle=Path(spec["skill_bundle"]), skill_digest=spec["skill_sha256"])
    skill = (tmp_path / "skill/SKILL.md").read_text()
    message = {"type": "message", "role": "user", "content": "Ordinary history."}
    ordinary = json.dumps({**BODY, "input": [message] * 50}).encode()
    exposed = json.dumps(
        {**BODY, "input": [message] * 49 + [{**message, "content": skill}]}
    ).encode()

    @contextmanager
    def broker(_auth, directory, **kwargs):
        directory.mkdir(mode=0o700)
        try:
            for index in range(1024):
                kwargs["request_observer"](exposed if index in (0, 1023) else ordinary)
            yield SimpleNamespace(
                socket_path=directory / "inference.sock",
                clean=True,
                receipts=[
                    {"event": "request", "outcome": "complete", "upstream_status": 200}
                    for _ in range(1024)
                ],
            )
        finally:
            directory.rmdir()

    monkeypatch.setattr(runner, "broker_scope", broker)
    report = invoke(tmp_path, route)
    assert report["status"] == "completed", report.get("error")
    assert report["request_count"] == 1024
    assert report["request_observations_compacted"] is True
    assert (tmp_path / "run/native-run.json").stat().st_size < 4 * 1024 * 1024
    audit_raw = (tmp_path / "run/native-audit.json").read_bytes()
    assert 4 * 1024 * 1024 < len(audit_raw) < 32 * 1024 * 1024
    audit = json.loads(audit_raw)
    assert len(audit["request_observations"]) == 1024
    for index, row in enumerate(report["request_observations"]):
        original = audit["request_observations"][index]
        reference = row["metadata_audit"]
        metadata = {key: original[key] for key in reference["fields"]}
        assert reference["field"] == f"request_observations[{index}]"
        assert reference["bytes"] == len(runner._json(metadata))
        assert reference["sha256"] == hashlib.sha256(runner._json(metadata)).hexdigest()
        assert len(original["context"]["messages"]) == 50
        assert row["skill_body_observed"] is (index in (0, 1023))
        assert row["skill_body_observed"] == original["skill_body_observed"]
    verified, retained, _artifacts = _run_files(tmp_path / "run", mode="review")
    assert verified["status"] == "completed"
    assert retained["native-audit.json"] == audit_raw
