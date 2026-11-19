"""Opt-in research capability wiring; synthetic runtimes are not VM proof."""

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest
from test_runner import invoke
from test_runner import route as route
from test_study import prepared as prepared

from skilleval import runner, study
from skilleval.identity import file_digest


@pytest.fixture(autouse=True)
def research_catalog(route: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    route["catalog"].write_text(
        json.dumps(
            {
                "models": [
                    {
                        "slug": "gpt-6.1-sol",
                        "use_responses_lite": True,
                        "tool_mode": "code_mode_only",
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(runner, "CATALOG_SHA256", file_digest(route["catalog"]))


def test_native_web_search_is_explicit_and_retained(tmp_path: Path, route: dict[str, Any]) -> None:
    report = invoke(tmp_path, {**route, "web_search": True})
    assert report["status"] == "completed", report.get("error")
    assert tomllib.loads(report["native_config"])["web_search"] == "live"
    assert report["web_search"] is True
    assert report["effective_catalog_sha256"] != report["catalog_sha256"]
    assert json.loads(route["catalog"].read_text())["models"][0]["use_responses_lite"] is True


def test_existing_native_route_keeps_search_disabled(tmp_path: Path, route: dict[str, Any]) -> None:
    report = invoke(tmp_path, route)
    assert tomllib.loads(report["native_config"])["web_search"] == "disabled"
    assert "web_search" not in report


def test_overflow_keeps_native_web_capability(
    tmp_path: Path, route: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from skilleval.judge import _run_files

    monkeypatch.setattr(runner, "MAX_NATIVE_AUDIT_BYTES", 16_384)
    report = invoke(tmp_path, {**route, "web_search": True})
    assert report["status"] == "partial"
    assert report.get("web_search") is True
    assert report["catalog_adaptation"] == "native-web-search-responses-v1"
    verified, _, _ = _run_files(tmp_path / "run", mode="review")
    assert verified["web_search"] is True


def test_paired_study_freezes_identical_web_capability(
    tmp_path: Path, prepared: dict[str, Any]
) -> None:
    original = json.loads(prepared["plan_path"].read_text())
    assert "web_search" not in original["spec"]
    spec = study.StudySpec.model_validate_json(json.dumps({**original["spec"], "web_search": True}))
    plan_path = tmp_path / "research-plan.json"
    frozen = study.prepare_study(spec, plan_path)
    study.run_study(
        plan_path,
        frozen["sha256"],
        prepared["output"],
        auth_file=prepared["auth_file"],
        development=True,
    )
    dispatches = list(prepared["output"].glob("attempts/*/attempt-001/dispatch.json"))
    assert len(dispatches) == 2
    assert {json.loads(p.read_text())["assignment"]["arm"] for p in dispatches} == {
        "absent",
        "available",
    }
    for dispatch in dispatches:
        assert json.loads(dispatch.read_text())["native_expected"]["web_search"] is True
        report = json.loads((dispatch.parent / "native/native-run.json").read_text())
        assert report["web_search"] is True
        assert tomllib.loads(report["native_config"])["web_search"] == "live"
