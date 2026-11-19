"""Fixed native judge choices; legacy packets retain their original Sol identity."""

from dataclasses import dataclass
from typing import Any

from skilleval.errors import SkillEvalError

DEFAULT_JUDGE_PROFILE = "codex-sol"


@dataclass(frozen=True)
class JudgeProfile:
    name: str
    provider: str
    model: str
    effort: str
    harness: str
    display_name: str


_PROFILES = {
    "codex-sol": JudgeProfile("codex-sol", "openai", "gpt-6.1-sol", "high", "codex", "Codex"),
    "claude-opus": JudgeProfile(
        "claude-opus", "anthropic", "claude-opus-5-5", "high", "claude-code", "Claude Code"
    ),
}
JUDGE_PROFILES = tuple(_PROFILES)


def resolve_profile(name: str = DEFAULT_JUDGE_PROFILE) -> JudgeProfile:
    if not isinstance(name, str) or name not in _PROFILES:
        raise SkillEvalError("unsupported judge profile; choose codex-sol or claude-opus")
    return _PROFILES[name]


def profile_metadata(profile: JudgeProfile) -> dict[str, str]:
    """Omit the legacy choice so its packet bytes and digests remain unchanged."""
    return {} if profile.name == DEFAULT_JUDGE_PROFILE else {"judge_profile": profile.name}


def metadata_profile(meta: Any) -> JudgeProfile:
    if not isinstance(meta, dict):
        raise SkillEvalError("invalid judge packet metadata")
    return resolve_profile(meta.get("judge_profile", DEFAULT_JUDGE_PROFILE))
