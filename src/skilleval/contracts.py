"""Provisional versioned records; these validate structure, not semantic truth."""

from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class Record(BaseModel):
    """Reject fields this version cannot faithfully preserve."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: Literal[1] = 1


class Task(Record):
    task_id: Identifier
    kind: Literal["review", "repair", "implementation"]
    population: Literal["development", "holdout"]
    evidence_sha256: Digest
    description: str = Field(min_length=1)


class Variant(Record):
    variant_id: Identifier
    skill_mode: Literal["absent", "available", "forced"]
    skill_sha256: Digest | None = None

    @model_validator(mode="after")
    def require_skill_identity(self) -> "Variant":
        if (self.skill_mode == "absent") != (self.skill_sha256 is None):
            raise ValueError("only absent treatments omit the skill digest")
        return self


class Experiment(Record):
    experiment_id: Identifier
    task_ids: tuple[Identifier, ...] = Field(min_length=1)
    harness: Literal["codex", "pi"]
    harness_version: str = Field(min_length=1)
    model: str = Field(min_length=1)
    effort: str = Field(min_length=1)
    variants: tuple[Variant, ...] = Field(min_length=2)
    repetitions: int = Field(ge=1, le=100)
    order_seed: int = Field(ge=0)
    timeout_seconds: int = Field(ge=1, le=86400)
    primary_outcomes: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_cells(self) -> "Experiment":
        if len(set(self.task_ids)) != len(self.task_ids):
            raise ValueError("task IDs must be unique")
        ids = [variant.variant_id for variant in self.variants]
        if len(set(ids)) != len(ids):
            raise ValueError("variant IDs must be unique")
        if not any(variant.skill_mode == "absent" for variant in self.variants):
            raise ValueError("a comparison needs an absent-skill baseline")
        if not any(variant.skill_mode != "absent" for variant in self.variants):
            raise ValueError("a comparison needs a skill treatment")
        return self


class Usage(Record):
    input_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    reported_cost_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class Attempt(Record):
    attempt_id: Identifier
    experiment_id: Identifier | None = None
    task_id: Identifier
    variant_id: Identifier | None = None
    repetition: int | None = Field(default=None, ge=1)
    evidence_sha256: Digest
    origin: Literal["native", "historical-import"]
    execution: Literal["completed", "failed", "interrupted", "unknown"]
    consultation: Literal["observed", "not-observed", "unmeasured"] = "unmeasured"
    usage: Usage = Field(default_factory=Usage)


class Citation(Record):
    path: str = Field(min_length=1)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)

    @model_validator(mode="after")
    def valid_span(self) -> "Citation":
        parsed = PurePosixPath(self.path)
        if (
            parsed.is_absolute()
            or ".." in parsed.parts
            or "\\" in self.path
            or "\x00" in self.path
            or str(parsed) != self.path
            or self.path == "."
        ):
            raise ValueError("citation path must be a normalized relative path")
        if self.line_end < self.line_start:
            raise ValueError("citation span is reversed")
        return self


class FindingJudgment(Record):
    finding_index: int = Field(ge=0)
    verdict: Literal["supported", "unsupported", "unresolved", "duplicate"]
    reasoning: str = Field(min_length=1)
    citations: tuple[Citation, ...] = ()


class Assessment(Record):
    assessment_id: Identifier
    subject_id: Identifier
    evidence_sha256: Digest
    rubric_version: str = Field(min_length=1)
    assessor_kind: Literal["human", "llm"]
    assessor: str = Field(min_length=1)
    model: str | None = None
    independent_blind: bool = False
    judgments: tuple[FindingJudgment, ...]
    limitations: tuple[str, ...] = ()
    supersedes: Identifier | None = None

    @model_validator(mode="after")
    def assessor_contract(self) -> "Assessment":
        if self.assessor_kind == "llm" and (self.model is None or not self.model.strip()):
            raise ValueError("LLM assessments require model identity")
        indices = [judgment.finding_index for judgment in self.judgments]
        if len(set(indices)) != len(indices):
            raise ValueError("finding judgments must have unique indices")
        if self.supersedes == self.assessment_id:
            raise ValueError("an assessment cannot supersede itself")
        return self
