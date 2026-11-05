"""Native workload deadlines, separate from provider request-count budgets."""

from skilleval.errors import SkillEvalError

TASK_TIMEOUT_SECONDS = 1800
JUDGE_TIMEOUT_SECONDS = 900
LEGACY_STUDY_TIMEOUT_SECONDS = 600
GATEWAY_SETUP_ALLOWANCE_SECONDS = 900
GATEWAY_HEADROOM_SECONDS = 120
MIN_GATEWAY_HEADROOM_SECONDS = 60
MAX_GATEWAY_LIFETIME_SECONDS = (
    GATEWAY_SETUP_ALLOWANCE_SECONDS + TASK_TIMEOUT_SECONDS + GATEWAY_HEADROOM_SECONDS
)


def native_deadline(mode: str, timeout_seconds: int | None = None) -> int:
    """Select the mode default or validate an explicitly shorter deadline."""
    if mode not in {"review", "repair", "judge"}:
        raise SkillEvalError("invalid native workload mode")
    ceiling = JUDGE_TIMEOUT_SECONDS if mode == "judge" else TASK_TIMEOUT_SECONDS
    if timeout_seconds is None:
        return ceiling
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= ceiling:
        raise SkillEvalError("invalid native workload deadline")
    return timeout_seconds
