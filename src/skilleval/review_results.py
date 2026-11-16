"""Attributed result synthesis; checked citations do not establish semantic truth."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from skilleval.contracts import Digest, Identifier, Record
from skilleval.errors import SkillEvalError
from skilleval.grading import _parse_json, _read_regular

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=16000)]
MAX_RESPONSE_CHARS = 10000
AnswerText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_RESPONSE_CHARS)
]
EvidenceIds = Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=100)]
SOURCE_LIMIT = 10 * 1024 * 1024
PACKET_LIMIT = 8 * 1024 * 1024


class ReviewAuthor(Record):
    name: Text
    role: Literal["agent", "human"]
    model: Text | None = None


class ReviewSummary(Record):
    headline: Text
    conclusion: Text
    scope: Text
    limitations: tuple[Text, ...] = Field(min_length=1, max_length=50)
    evidence: EvidenceIds


class ReviewArm(Record):
    id: Identifier
    label: Text
    status: Text
    quality: tuple[Text, ...] = Field(min_length=1, max_length=50)
    usage: tuple[Text, ...] = Field(min_length=1, max_length=50)
    cost: tuple[Text, ...] = Field(min_length=1, max_length=50)
    timing: tuple[Text, ...] = Field(min_length=1, max_length=50)
    evidence: EvidenceIds


class ReviewException(Record):
    id: Identifier
    title: Text
    category: Literal["human-needed", "agent-follow-up", "qualified-unknown"]
    question: Text
    why_human: Text | None = None
    investigation: tuple[Text, ...] = Field(min_length=1, max_length=50)
    impact: Text
    evidence: EvidenceIds
    options: tuple[AnswerText, ...] = Field(default=(), max_length=10)

    @model_validator(mode="after")
    def human_reason(self) -> "ReviewException":
        if (self.category == "human-needed") != (self.why_human is not None):
            raise ValueError("only a human-needed question must explain why human input is needed")
        if self.options and self.category != "human-needed":
            raise ValueError("answer options belong only to human-needed questions")
        return self


class ReviewSource(Record):
    id: Identifier
    label: Text
    sha256: Digest


class ReviewSourcePath(ReviewSource):
    path: Text


class ReviewCitation(Record):
    id: Identifier
    source_id: Identifier
    label: Text
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def bounded_range(self) -> "ReviewCitation":
        if not 0 <= self.end_line - self.start_line < 200:
            raise ValueError("citation must span between one and 200 lines")
        return self


class ReviewExcerpt(Record):
    id: Identifier
    label: Text
    locator: Text
    # JSONL and embedded command output can have large, valid single lines.
    # Source reads and the complete serialized packet already have byte bounds.
    excerpt: str
    source_sha256: Digest


def _unique_ids(rows: Any, label: str) -> set[str]:
    ids = [row.id for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate {label} IDs")
    return set(ids)


class ReviewContent(Record):
    title: Text
    author: ReviewAuthor
    summary: ReviewSummary
    arms: tuple[ReviewArm, ...] = Field(min_length=1, max_length=20)
    exceptions: tuple[ReviewException, ...] = Field(max_length=100)

    def check_references(self, evidence_ids: set[str]) -> None:
        _unique_ids(self.arms, "arm")
        _unique_ids(self.exceptions, "exception")
        rows: tuple[ReviewSummary | ReviewArm | ReviewException, ...] = (
            self.summary,
            *self.arms,
            *self.exceptions,
        )
        for row in rows:
            if not set(row.evidence) <= evidence_ids:
                raise ValueError("unknown evidence reference")


class ReviewSpec(ReviewContent):
    sources: tuple[ReviewSourcePath, ...] = Field(min_length=1, max_length=100)
    citations: tuple[ReviewCitation, ...] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def references(self) -> "ReviewSpec":
        sources = _unique_ids(self.sources, "source")
        citations = _unique_ids(self.citations, "citation")
        if any(row.source_id not in sources for row in self.citations):
            raise ValueError("citation references unknown source")
        self.check_references(citations)
        return self


class ReviewPacket(ReviewContent):
    packet_id: Digest
    generated_at: Text
    source_spec_sha256: Digest
    sources: tuple[ReviewSource, ...] = Field(min_length=1, max_length=100)
    evidence: tuple[ReviewExcerpt, ...] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def references(self) -> "ReviewPacket":
        _unique_ids(self.sources, "source")
        self.check_references(_unique_ids(self.evidence, "evidence"))
        if any(row.source_sha256 not in {s.sha256 for s in self.sources} for row in self.evidence):
            raise ValueError("excerpt references unknown source digest")
        return self


def packet_identity(core: dict[str, Any]) -> str:
    raw = json.dumps(core, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _excerpt(
    citation: ReviewCitation, source: ReviewSourcePath, lines: list[str]
) -> dict[str, Any]:
    if citation.end_line > len(lines):
        raise SkillEvalError(f"citation {citation.id} extends beyond retained source")
    excerpt = "\n".join(lines[citation.start_line - 1 : citation.end_line])
    return {
        "id": citation.id,
        "label": citation.label,
        "locator": f"{source.label}, lines {citation.start_line}-{citation.end_line}",
        "excerpt": excerpt,
        "source_sha256": source.sha256,
    }


def build_review(spec_path: Path, output: Path) -> dict[str, Any]:
    """Freeze a selected agent/human synthesis with hash-checked text excerpts."""
    raw = _read_regular(spec_path, PACKET_LIMIT)
    spec = ReviewSpec.model_validate_json(json.dumps(_parse_json(raw)))
    sources = {row.id: row for row in spec.sources}
    lines = {}
    for source in spec.sources:
        source_path = Path(source.path)
        if not source_path.is_absolute():
            source_path = spec_path.parent / source_path
        contents = _read_regular(source_path, SOURCE_LIMIT)
        if hashlib.sha256(contents).hexdigest() != source.sha256:
            raise SkillEvalError(f"review source {source.id} digest differs")
        try:
            lines[source.id] = contents.decode("utf-8").splitlines()
        except UnicodeError:
            raise SkillEvalError(f"review source {source.id} must be UTF-8 text") from None
    core = spec.model_dump(mode="json", exclude={"sources", "citations"})
    core.update(
        generated_at=datetime.now(UTC).isoformat(),
        source_spec_sha256=hashlib.sha256(raw).hexdigest(),
        sources=[row.model_dump(mode="json", exclude={"path"}) for row in spec.sources],
        evidence=[
            _excerpt(row, sources[row.source_id], lines[row.source_id]) for row in spec.citations
        ],
    )
    # Normalize before hashing so defaults never change the identity on readback.
    packet = ReviewPacket.model_validate_json(json.dumps({**core, "packet_id": "0" * 64}))
    core = packet.model_dump(mode="json", exclude={"packet_id"})
    value = {**core, "packet_id": packet_identity(core)}
    encoded = (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()
    if len(encoded) > PACKET_LIMIT:
        raise SkillEvalError("review packet exceeds size limit")
    # Validate everything before claiming the new output directory. Never update an older packet.
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    (output / "packet.json").write_bytes(encoded)
    (output / "source-spec.json").write_bytes(raw)
    return review_status(value, output)


def load_packet(directory: Path) -> dict[str, Any]:
    """Read only the frozen projection; serving does not traverse original evidence."""
    raw = _read_regular(directory / "packet.json", PACKET_LIMIT)
    value = _parse_json(raw)
    packet = ReviewPacket.model_validate_json(json.dumps(value))
    core = dict(value)
    supplied = core.pop("packet_id")
    if packet_identity(core) != supplied:
        raise SkillEvalError("review packet identity differs")
    return packet.model_dump(mode="json")


def review_status(packet: dict[str, Any], directory: Path) -> dict[str, Any]:
    categories = [row["category"] for row in packet["exceptions"]]
    return {
        "status": "prepared",
        "packet_id": packet["packet_id"],
        "directory": str(directory.absolute()),
        "human_questions": categories.count("human-needed"),
        "agent_follow_ups": categories.count("agent-follow-up"),
        "qualified_unknowns": categories.count("qualified-unknown"),
        "semantic_truth": "not-established-by-citation-validation",
    }
