import pytest
from pydantic import ValidationError

from skilleval.contracts import Assessment, Attempt, Citation, Experiment, Variant

DIGEST = "a" * 64


def test_skill_treatment_requires_identity() -> None:
    with pytest.raises(ValidationError):
        Variant(variant_id="on", skill_mode="available")
    with pytest.raises(ValidationError):
        Variant(variant_id="off", skill_mode="absent", skill_sha256=DIGEST)


def test_duplicate_variants_cannot_be_counted_as_a_pair() -> None:
    variant = Variant(variant_id="off", skill_mode="absent")
    with pytest.raises(ValidationError):
        Experiment(
            experiment_id="pilot",
            task_ids=("task",),
            harness="codex",
            harness_version="pinned",
            model="model",
            effort="high",
            variants=(variant, variant),
            repetitions=1,
            order_seed=1,
            timeout_seconds=30,
            primary_outcomes=("quality",),
        )


def test_missing_usage_remains_unknown_after_roundtrip() -> None:
    record = Attempt(
        attempt_id="run",
        task_id="task",
        evidence_sha256=DIGEST,
        origin="historical-import",
        execution="unknown",
    )
    restored = Attempt.model_validate_json(record.model_dump_json())
    assert restored.usage.input_tokens is None
    assert restored.variant_id is None
    assert restored.consultation == "unmeasured"


def test_citation_cannot_escape_evidence_or_reverse_span() -> None:
    for path in ("../secret", "/etc/passwd", "a/../b", "a\\b"):
        with pytest.raises(ValidationError):
            Citation(path=path, line_start=1, line_end=2)
    with pytest.raises(ValidationError):
        Citation(path="case/source/code.py", line_start=5, line_end=1)


def test_llm_assessor_requires_model_identity() -> None:
    with pytest.raises(ValidationError):
        Assessment(
            assessment_id="review-1",
            subject_id="report-1",
            evidence_sha256=DIGEST,
            rubric_version="review-v1",
            assessor_kind="llm",
            assessor="judge",
            judgments=(),
        )
