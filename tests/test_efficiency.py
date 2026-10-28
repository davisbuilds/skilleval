"""Descriptive arithmetic controls; no provider or task-quality inference."""

from skilleval.efficiency import efficiency_ledger, pair_efficiency


def outcome(tokens=100, seconds=10, status="completed", cost="0.1", schedule=None):
    return {
        "status": status,
        "cleanup_complete": True,
        "skill_consultation": "unknown",
        "usage_accounting": {
            "status": "complete",
            "input_tokens": tokens,
            "cached_input_tokens": 20,
            "uncached_input_tokens": tokens - 20,
            "output_tokens": 10,
            "total_tokens": tokens + 10,
        },
        "timings": {"agent_seconds": seconds},
        "cost": {
            "status": "estimated",
            "estimated_api_equivalent_usd": cost,
            "schedule": schedule or {"model": "synthetic", "rate_scope": "hypothetical"},
        },
    }


def test_pair_delta_uses_on_minus_off_without_quality_inference():
    result = pair_efficiency(outcome(120), outcome(100), comparable=True)
    assert result["comparable"] is True
    assert result["token_deltas"]["total_tokens"] == 20
    assert result["task_quality_difference"] is None


def test_ledger_counts_failed_first_attempt_and_completed_retry():
    result = efficiency_ledger(
        [
            {"arm": "available", "attempts": [outcome(100, status="failed"), outcome(200)]},
        ]
    )
    arm = result["arms"]["available"]
    assert arm["attempt_count"] == 2
    assert arm["tokens"]["input_tokens"]["total"] == 300
    assert arm["status_counts"] == {"failed": 1, "completed": 1}


def test_cheap_failed_or_unclean_attempt_never_looks_like_clean_pair_savings():
    for cheap in (
        outcome(20, 1, status="failed", cost="0.001"),
        {**outcome(20, 1, cost="0.001"), "cleanup_complete": False},
    ):
        pair = pair_efficiency(cheap, outcome(100, 20), comparable=True)
        assert pair["comparable"] is False
        assert all(value is None for value in pair["token_deltas"].values())
        assert pair["estimated_api_equivalent_usd_delta"] is None
        assert pair["timing_deltas_seconds"]["agent_seconds"] is None
    pair = pair_efficiency(outcome(), outcome(), comparable=False)
    assert pair["token_deltas"]["input_tokens"] is None


def test_missing_partial_and_legacy_telemetry_stays_unknown():
    partial = outcome()
    partial["usage_accounting"]["status"] = "incomplete"
    partial["cost"]["status"] = "unknown"
    legacy = {"status": "completed", "cleanup_complete": True, "elapsed_seconds": 42}
    pair = pair_efficiency(partial, outcome(), comparable=True)
    assert pair["token_deltas"]["input_tokens"] is None
    assert pair["estimated_api_equivalent_usd_delta"] is None
    result = efficiency_ledger([{"arm": "available", "attempts": [partial, legacy]}])
    arm = result["arms"]["available"]
    assert arm["tokens"]["total_tokens"] == {
        "known_subtotal": None,
        "total": None,
        "known_attempt_count": 0,
        "unknown_attempt_count": 2,
    }
    assert arm["timings_seconds"]["total_seconds"]["total"] is None
    assert arm["api_equivalent_cost"]["total_usd"] is None
    assert arm["api_equivalent_cost"]["unknown_attempt_count"] == 2


def test_known_subtotals_do_not_masquerade_as_full_totals():
    unfinished = {"status": "unfinished", "cleanup_complete": False}
    arm = efficiency_ledger(
        [
            {"arm": "absent", "attempts": [outcome(), unfinished]},
        ]
    )["arms"]["absent"]
    metric = arm["tokens"]["input_tokens"]
    assert metric["known_subtotal"] == 100
    assert metric["total"] is None
    assert metric["unknown_attempt_count"] == 1
    assert arm["unfinished_attempt_count"] == 1
    assert arm["cleanup_incomplete_attempt_count"] == 1
    assert arm["api_equivalent_cost"]["schedule_groups"][0]["known_subtotal_usd"] == "0.1"
    assert arm["api_equivalent_cost"]["total_usd"] is None


def test_different_price_schedules_never_pool_or_compare():
    first = outcome(cost="0.3")
    second = outcome(cost="0.2", schedule={"model": "synthetic", "rate_scope": "different"})
    pair = pair_efficiency(first, second, comparable=True)
    assert pair["token_deltas"]["total_tokens"] == 0
    assert pair["estimated_api_equivalent_usd_delta"] is None
    cost = efficiency_ledger(
        [
            {"arm": "available", "attempts": [first, second]},
        ]
    )["arms"]["available"]["api_equivalent_cost"]
    assert len(cost["schedule_groups"]) == 2
    assert cost["unknown_attempt_count"] == 0
    assert cost["total_usd"] is None


def test_exact_decimal_cost_difference_and_all_attempt_sum():
    first, second = outcome(cost="0.3"), outcome(cost="0.2")
    pair = pair_efficiency(first, second, comparable=True)
    assert pair["estimated_api_equivalent_usd_delta"] == "0.1"
    pair = pair_efficiency(second, first, comparable=True)
    assert pair["estimated_api_equivalent_usd_delta"] == "-0.1"
    cost = efficiency_ledger(
        [
            {"arm": "available", "attempts": [first, second]},
        ]
    )["arms"]["available"]["api_equivalent_cost"]
    assert cost["total_usd"] == "0.5"
    assert cost["known_attempt_count"] == 2
    assert cost["cash_cost_usd"] is None


def test_empty_arms_and_unstarted_cells_have_no_fabricated_zero_totals():
    empty = efficiency_ledger([])
    for arm in empty["arms"].values():
        assert arm["attempt_count"] == 0
        assert arm["tokens"]["input_tokens"]["total"] is None
        assert arm["tokens"]["input_tokens"]["known_subtotal"] is None
        assert arm["api_equivalent_cost"]["total_usd"] is None
    arm = efficiency_ledger([{"arm": "available", "attempts": []}])["arms"]["available"]
    assert arm["not_started_cell_count"] == 1
    pair = pair_efficiency(None, outcome(), comparable=True)
    assert pair["comparable"] is False


def test_zero_is_distinct_from_missing_and_ignored_skill_attempts_stay_included():
    zero = outcome(tokens=20, seconds=0, cost="0")
    zero["skill_consultation"] = "ignored"
    arm = efficiency_ledger(
        [
            {"arm": "available", "attempts": [zero]},
        ]
    )["arms"]["available"]
    assert arm["attempt_count"] == 1
    assert arm["tokens"]["uncached_input_tokens"]["total"] == 0
    assert arm["timings_seconds"]["agent_seconds"]["total"] == 0
    assert arm["api_equivalent_cost"]["total_usd"] == "0"


def test_nonfinite_negative_and_boolean_measurements_are_not_known_spend():
    for value in (float("nan"), float("inf"), -1, True, "1"):
        bad = outcome()
        bad["timings"]["agent_seconds"] = value
        bad["usage_accounting"]["input_tokens"] = value
        pair = pair_efficiency(bad, outcome(), comparable=True)
        assert pair["timing_deltas_seconds"]["agent_seconds"] is None
        assert pair["token_deltas"]["input_tokens"] is None
    for value in ("NaN", "Infinity", "-1", True, 0.1):
        bad = outcome()
        bad["cost"]["estimated_api_equivalent_usd"] = value
        arm = efficiency_ledger([{"arm": "available", "attempts": [bad]}])["arms"]["available"]
        assert arm["api_equivalent_cost"]["unknown_attempt_count"] == 1
        assert arm["api_equivalent_cost"]["total_usd"] is None


def test_separate_arms_and_stage_timings_do_not_use_legacy_whole_attempt_elapsed():
    on, off = outcome(seconds=15), outcome(seconds=10)
    on["elapsed_seconds"], off["elapsed_seconds"] = 500, 900
    on["timings"].update(setup_seconds=2, total_seconds=30)
    off["timings"].update(setup_seconds=5, total_seconds=40)
    pair = pair_efficiency(on, off, comparable=True)
    assert pair["timing_deltas_seconds"]["agent_seconds"] == 5
    assert pair["timing_deltas_seconds"]["setup_seconds"] == -3
    assert pair["timing_deltas_seconds"]["total_seconds"] == -10
    ledger = efficiency_ledger(
        [
            {"arm": "available", "attempts": [on]},
            {"arm": "absent", "attempts": [off]},
        ]
    )
    assert ledger["arms"]["available"]["timings_seconds"]["agent_seconds"]["total"] == 15
    assert ledger["arms"]["absent"]["timings_seconds"]["agent_seconds"]["total"] == 10
    assert ledger["task_quality"] == "unassessed"
    assert ledger["uncertainty"] == "not-estimated"
