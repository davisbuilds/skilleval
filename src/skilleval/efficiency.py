"""Descriptive paired efficiency and all-attempt operational accounting.

These summaries do not estimate uncertainty, correctness, or skill benefit.
Missing observations stay unknown; failures and retries remain operational spend.
"""

from __future__ import annotations

import json
import math
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

TOKEN_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "uncached_input_tokens",
    "output_tokens",
    "total_tokens",
)
TIME_FIELDS = (
    "agent_seconds",
    "setup_seconds",
    "native_step_seconds",
    "collection_seconds",
    "cleanup_seconds",
    "total_seconds",
)


def _tokens(attempt: dict[str, Any], field: str) -> int | None:
    usage = attempt.get("usage_accounting")
    if not isinstance(usage, dict) or usage.get("status") != "complete":
        return None
    value = usage.get(field)
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def _seconds(attempt: dict[str, Any], field: str) -> int | float | None:
    timings = attempt.get("timings")
    value = timings.get(field) if isinstance(timings, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


def _cost(attempt: dict[str, Any]) -> tuple[str, dict[str, Any], Decimal] | None:
    cost = attempt.get("cost")
    if not isinstance(cost, dict) or cost.get("status") != "estimated":
        return None
    schedule, raw = cost.get("schedule"), cost.get("estimated_api_equivalent_usd")
    if not isinstance(schedule, dict) or not schedule or not isinstance(raw, str) or len(raw) > 128:
        return None
    try:
        key = json.dumps(schedule, sort_keys=True, separators=(",", ":"), allow_nan=False)
        amount = Decimal(raw)
        if not amount.is_finite() or amount < 0 or (amount and abs(amount.adjusted()) > 100):
            return None
    except (ValueError, TypeError, InvalidOperation):
        return None
    return key, schedule, amount


def _money(value: Decimal) -> str:
    if not value:
        return "0"
    rendered = format(value, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def pair_efficiency(
    on: dict[str, Any] | None, off: dict[str, Any] | None, *, comparable: bool
) -> dict[str, Any]:
    """Compare clean completed primaries only; direction is always ON minus OFF."""
    clean = all(
        arm is not None and arm.get("status") == "completed" and arm.get("cleanup_complete") is True
        for arm in (on, off)
    )
    eligible = comparable and clean
    result: dict[str, Any] = {
        "direction": "on-minus-off",
        "comparable": eligible,
        "token_deltas": dict.fromkeys(TOKEN_FIELDS),
        "estimated_api_equivalent_usd_delta": None,
        "timing_deltas_seconds": dict.fromkeys(TIME_FIELDS),
        "task_quality_difference": None,
        "task_quality": "unassessed",
        "uncertainty": "not-estimated",
        "reasons": [],
    }
    if not eligible or on is None or off is None:
        result["reasons"] = ["comparable clean completed primary executions required"]
        return result
    for fields, reader, destination in (
        (TOKEN_FIELDS, _tokens, "token_deltas"),
        (TIME_FIELDS, _seconds, "timing_deltas_seconds"),
    ):
        for field in fields:
            left, right = reader(on, field), reader(off, field)
            if left is not None and right is not None:
                result[destination][field] = left - right
    left_cost, right_cost = _cost(on), _cost(off)
    if left_cost is not None and right_cost is not None and left_cost[0] == right_cost[0]:
        with localcontext() as context:
            context.prec = 256
            result["estimated_api_equivalent_usd_delta"] = _money(left_cost[2] - right_cost[2])
    else:
        result["reasons"].append("complete cost estimates with identical schedules required")
    return result


def _summary(values: list[int | float | None]) -> dict[str, Any]:
    known = [value for value in values if value is not None]
    subtotal = sum(known) if known else None
    if isinstance(subtotal, float) and not math.isfinite(subtotal):
        subtotal = None
    return {
        "known_subtotal": subtotal,
        "total": subtotal if len(known) == len(values) else None,
        "known_attempt_count": len(known),
        "unknown_attempt_count": len(values) - len(known),
    }


def efficiency_ledger(cells: list[dict[str, Any]]) -> dict[str, Any]:
    """Retain every recorded attempt, including failed, unfinished, and retry rows."""
    arms: dict[str, Any] = {}
    for arm in ("available", "absent"):
        selected = [cell for cell in cells if cell.get("arm") == arm]
        attempts = [attempt for cell in selected for attempt in cell.get("attempts", [])]
        statuses: dict[str, int] = {}
        groups: dict[str, dict[str, Any]] = {}
        unknown_cost = 0
        for attempt in attempts:
            status = attempt.get("status")
            status = status if isinstance(status, str) else "unknown"
            statuses[status] = statuses.get(status, 0) + 1
            cost = _cost(attempt)
            if cost is None:
                unknown_cost += 1
                continue
            key, schedule, amount = cost
            group = groups.setdefault(
                key,
                {
                    "schedule": schedule,
                    "known_attempt_count": 0,
                    "amount": Decimal(0),
                },
            )
            with localcontext() as context:
                context.prec = 256
                group["amount"] += amount
            group["known_attempt_count"] += 1
        schedule_groups = [
            {
                "schedule": value["schedule"],
                "known_attempt_count": value["known_attempt_count"],
                "known_subtotal_usd": _money(value["amount"]),
            }
            for _, value in sorted(groups.items())
        ]
        arms[arm] = {
            "cell_count": len(selected),
            "not_started_cell_count": sum(not cell.get("attempts") for cell in selected),
            "attempt_count": len(attempts),
            "status_counts": statuses,
            "unfinished_attempt_count": statuses.get("unfinished", 0),
            "cleanup_incomplete_attempt_count": sum(
                attempt.get("cleanup_complete") is not True for attempt in attempts
            ),
            "tokens": {
                field: _summary([_tokens(a, field) for a in attempts]) for field in TOKEN_FIELDS
            },
            "timings_seconds": {
                field: _summary([_seconds(a, field) for a in attempts]) for field in TIME_FIELDS
            },
            "api_equivalent_cost": {
                "schedule_groups": schedule_groups,
                "unknown_attempt_count": unknown_cost,
                "known_attempt_count": len(attempts) - unknown_cost,
                "total_usd": schedule_groups[0]["known_subtotal_usd"]
                if len(schedule_groups) == 1 and unknown_cost == 0
                else None,
                "cash_cost_usd": None,
            },
        }
    return {
        "schema_version": 1,
        "scope": "all-recorded-attempts-including-retries",
        "arms": arms,
        "task_quality": "unassessed",
        "uncertainty": "not-estimated",
        "limits": [
            "Operational spend includes failures and retries.",
            "Different price schedules are never pooled.",
            "Missing telemetry and unstarted cells are not zero-cost observations.",
        ],
    }
