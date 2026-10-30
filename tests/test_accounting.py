"""Synthetic arithmetic and transcript controls; no provider or runtime calibration."""

from datetime import date
from decimal import Decimal, localcontext

import pytest

from skilleval.accounting import (
    PriceSchedule,
    estimate_api_equivalent_cost,
    normalize_codex_usage,
)
from skilleval.errors import SkillEvalError

IDENTITY = {"observed_version": "0.164.0", "baseline_match": True}
MODEL = "synthetic-model"


def transcript(input_tokens=1000, cached_input_tokens=200, output_tokens=50):
    return [
        {"type": "thread.started", "thread_id": "synthetic-thread"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}},
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": input_tokens,
                "cached_input_tokens": cached_input_tokens,
                "output_tokens": output_tokens,
            },
        },
    ]


def normalize(events=None, receipts=None):
    return normalize_codex_usage(
        transcript() if events is None else events,
        codex_identity=IDENTITY,
        model=MODEL,
        provider_observations=receipts,
    )


def schedule():
    return PriceSchedule(
        model=MODEL,
        source_url="https://example.com/synthetic-pricing",
        retrieved_on=date(2026, 10, 28),
        input_per_million=Decimal("2"),
        cached_input_per_million=Decimal("0.5"),
        output_per_million=Decimal("8"),
    )


def test_terminal_snapshot_counts_cache_once_and_retains_provenance():
    result = normalize()
    assert result["status"] == "complete"
    assert result["input_tokens"] == 1000
    assert result["cached_input_tokens"] == 200
    assert result["uncached_input_tokens"] == 800
    assert result["output_tokens"] == 50
    assert result["total_tokens"] == 1050
    assert result["provenance"]["terminal_event_index"] == 3
    assert result["provenance"]["codex_identity"] == IDENTITY
    assert result["provider_reconciliation"]["status"] == "unknown"


def test_explicit_exec_surface_preserves_legacy_default_provenance():
    result = normalize_codex_usage(
        transcript(), codex_identity=IDENTITY, model=MODEL, surface="codex-exec-json"
    )
    assert result == normalize()
    assert result["provenance"] == {
        "surface": "codex-exec-json",
        "record": "turn.completed.usage",
        "form": "single terminal cumulative thread snapshot",
        "codex_identity": IDENTITY,
        "source_revision": "1c0c45bfc372fb7d7d581508fdef5eef8235eab7",
        "source_url": (
            "https://github.com/openai/codex/blob/"
            "1c0c45bfc372fb7d7d581508fdef5eef8235eab7/"
            "codex-rs/exec/src/event_processor_with_jsonl_output.rs#L118-L128"
        ),
        "source_binary_equivalence": "unverified",
        "measurement": "native-reported; not calibrated provider billing",
        "terminal_event_index": 3,
    }


def test_app_server_surface_preserves_counts_with_distinct_transport_provenance():
    events = transcript()
    events[-1]["usage"].update(
        total_tokens=1050, cache_write_input_tokens=0, reasoning_output_tokens=20
    )
    result = normalize_codex_usage(
        events,
        codex_identity=IDENTITY,
        model=MODEL,
        provider_observations=[receipt()],
        surface="codex-app-server-jsonrpc-v2",
    )
    assert result["status"] == "complete"
    for field, value in {
        "input_tokens": 1000,
        "cached_input_tokens": 200,
        "uncached_input_tokens": 800,
        "output_tokens": 50,
        "total_tokens": 1050,
        "cache_write_input_tokens": 0,
        "reasoning_output_tokens": 20,
    }.items():
        assert result[field] == value
    assert result["provider_reconciliation"]["status"] == "matched"
    provenance = result["provenance"]
    assert provenance["surface"] == "codex-app-server-jsonrpc-v2"
    assert provenance["adapter"] == "app-server-v2-to-canonical-v1"
    assert provenance["record"] == "thread/tokenUsage/updated.params.tokenUsage.total"
    assert provenance["form"] == (
        "latest matched-thread cumulative snapshot before matched turn/completed"
    )
    assert provenance["source_revision"] == "6aaac694803004a63c6f393d153c96d22521a6b9"
    assert provenance["source_url"] == (
        "https://github.com/openai/codex/blob/6aaac694803004a63c6f393d153c96d22521a6b9/"
        "codex-rs/app-server/src/bespoke_event_handling.rs#L1602-L1615"
    )
    assert provenance["source_binary_equivalence"] == "unverified"
    assert provenance["terminal_event_index"] == 3
    assert estimate_api_equivalent_cost(result, schedule(), model=MODEL)["status"] == "estimated"


def test_app_server_surface_does_not_complete_missing_failed_or_duplicate_usage():
    cases = [
        ([], None, "unknown"),
        (transcript()[:-1], None, "incomplete"),
        ([*transcript()[:-1], {"type": "turn.completed"}], None, "incomplete"),
        ([*transcript()[:-1], {"type": "turn.failed"}], None, "incomplete"),
        ([*transcript(), transcript()[-1]], None, "invalid"),
        (transcript(), [{"line": 2, "error": "synthetic invalid JSON"}], "incomplete"),
    ]
    for events, parse_errors, expected in cases:
        result = normalize_codex_usage(
            events,
            codex_identity=IDENTITY,
            model=MODEL,
            parse_errors=parse_errors,
            surface="codex-app-server-jsonrpc-v2",
        )
        assert result["status"] == expected
        assert result["total_tokens"] is None
        assert result["provenance"]["surface"] == "codex-app-server-jsonrpc-v2"


def test_unknown_accounting_surface_is_rejected_even_without_events():
    with pytest.raises(SkillEvalError, match="unsupported Codex usage surface"):
        normalize_codex_usage([], codex_identity=IDENTITY, model=MODEL, surface="unknown")


def test_hypothetical_cost_uses_partition_and_is_never_cash_charge():
    result = estimate_api_equivalent_cost(normalize(), schedule(), model=MODEL)
    assert result["status"] == "estimated"
    assert result["estimated_api_equivalent_usd"] == "0.0021"
    assert result["cash_cost_usd"] is None


def test_cost_keeps_exact_digits_independently_of_callers_decimal_context():
    rates = schedule().model_copy(update={"input_per_million": Decimal("1.2345678901234567")})
    usage = normalize(transcript(input_tokens=2**63 - 1, cached_input_tokens=0, output_tokens=0))
    for precision in (8, 28, 64):
        with localcontext() as context:
            context.prec = precision
            result = estimate_api_equivalent_cost(usage, rates, model=MODEL)
        assert result["estimated_api_equivalent_usd"] == "11386878955363.4898790134062303720569"


def receipt(request_id=1, input_tokens=1000, output_tokens=50, **changes):
    return {
        "event": "request",
        "request_id": request_id,
        "model": MODEL,
        "outcome": "complete",
        "upstream_status": 200,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        **changes,
    }


def test_independent_provider_sums_reconcile_but_cache_remains_unchecked():
    result = normalize(receipts=[receipt(1, 400, 20), receipt(2, 600, 30)])
    assert result["status"] == "complete"
    provider = result["provider_reconciliation"]
    assert provider["status"] == "matched"
    assert provider["cached_input_status"] == "unknown"
    assert provider["input_tokens"] == 1000
    assert provider["max_input_tokens_per_request"] == 600


def test_independent_cache_details_can_match_or_contradict_native():
    row = receipt()
    row["usage"]["input_tokens_details"] = {"cached_tokens": 200}
    result = normalize(receipts=[row])
    assert result["provider_reconciliation"]["cached_input_status"] == "matched"
    row["usage"]["input_tokens_details"]["cached_tokens"] = 201
    result = normalize(receipts=[row])
    assert result["status"] == "invalid"
    assert result["input_tokens"] is None
    assert result["provider_reconciliation"]["status"] == "mismatch"


def test_repeated_terminal_snapshot_is_rejected_without_summing():
    events = transcript()
    events.append(events[-1].copy())
    result = normalize(events)
    assert result["status"] == "invalid"
    assert result["total_tokens"] is None


def test_multiple_turns_and_concatenated_threads_are_not_incremental_usage():
    for extra in (transcript()[1:], transcript()):
        result = normalize(transcript() + extra)
        assert result["status"] == "invalid"
        assert result["total_tokens"] is None


def test_absent_partial_failed_and_malformed_transcripts_stay_distinct():
    assert normalize([])["status"] == "unknown"
    assert normalize(transcript()[:-1])["status"] == "incomplete"
    assert normalize(transcript()[1:])["status"] == "incomplete"
    assert normalize([{"type": 3}])["status"] == "invalid"
    events = transcript()
    events.insert(-1, {"type": "error", "message": "synthetic failure"})
    assert normalize(events)["status"] == "incomplete"
    events = transcript()
    del events[-1]["usage"]["output_tokens"]
    assert normalize(events)["status"] == "incomplete"
    assert (
        normalize_codex_usage(
            transcript(),
            codex_identity=IDENTITY,
            model=MODEL,
            parse_errors=[{"line": 2, "error": "synthetic invalid JSON"}],
        )["status"]
        == "incomplete"
    )


def test_zero_fallback_requires_complete_independent_provider_observation():
    events = transcript(0, 0, 0)
    result = normalize(events)
    assert result["status"] == "unknown"
    assert result["total_tokens"] is None
    assert normalize(events, receipts=[receipt(1, 0, 0)])["total_tokens"] == 0
    assert normalize(events, receipts=[])["status"] == "incomplete"


def test_invalid_native_counts_do_not_become_zero_or_get_coerced():
    for value in (True, -1, 1.0, "1", None, 2**63):
        events = transcript()
        events[-1]["usage"]["input_tokens"] = value
        assert normalize(events)["status"] == "invalid"
    assert normalize(transcript(10, 11, 1))["status"] == "invalid"
    for key, value in (("reasoning_output_tokens", 51), ("total_tokens", 1250)):
        events = transcript()
        events[-1]["usage"][key] = value
        assert normalize(events)["status"] == "invalid"


def test_source_reference_does_not_claim_binary_equivalence():
    result = normalize_codex_usage(
        transcript(),
        codex_identity={"observed_version": "0.165.0"},
        model=MODEL,
    )
    assert result["status"] == "complete"
    assert result["provenance"]["source_binary_equivalence"] == "unverified"
    assert (
        normalize_codex_usage(transcript(), codex_identity={}, model=MODEL)["status"] == "unknown"
    )


def test_provider_duplicates_missing_counts_failures_and_model_mismatch():
    rows = [receipt(), receipt()]
    assert normalize(receipts=rows)["status"] == "invalid"
    assert normalize(receipts=[receipt(model="other-model")])["status"] == "invalid"
    assert normalize(receipts=[receipt(outcome="timeout")])["status"] == "incomplete"
    assert normalize(receipts=[receipt(usage=None)])["status"] == "incomplete"
    assert normalize(receipts=[receipt(input_tokens=999)])["status"] == "invalid"
    row = receipt()
    row["usage"]["total_tokens"] = 50
    assert normalize(receipts=[row])["provider_reconciliation"]["status"] == "invalid"


def test_unknown_usage_and_missing_schedule_never_become_zero_cost():
    assert (
        estimate_api_equivalent_cost(normalize(), None, model=MODEL)["estimated_api_equivalent_usd"]
        is None
    )
    assert (
        estimate_api_equivalent_cost(normalize([]), schedule(), model=MODEL)["status"] == "unknown"
    )


def test_schedule_exact_model_and_strict_public_rate_metadata():
    from pydantic import ValidationError

    with pytest.raises(ValueError, match="exact model"):
        estimate_api_equivalent_cost(normalize(), schedule(), model="other-model")
    base = schedule().model_dump()
    for changes in (
        {"input_per_million": Decimal("NaN")},
        {"input_per_million": Decimal("Infinity")},
        {"cached_input_per_million": Decimal("-1")},
        {"output_per_million": 1.5},
        {"currency": "EUR"},
        {"retrieved_on": "2026-10-28"},
        {"source_url": "http://example.com/prices"},
        {"source_url": "https://127.0.0.1/prices"},
        {"source_url": "https://user:password@example.com/prices"},
        {"model": " synthetic-model"},
        {"extra_rate": Decimal("1")},
    ):
        with pytest.raises(ValidationError):
            PriceSchedule.model_validate({**base, **changes})
    assert PriceSchedule.model_validate_json(schedule().model_dump_json()) == schedule()


def test_context_price_scope_requires_observed_per_request_inputs():
    limited = PriceSchedule.model_validate(
        {**schedule().model_dump(), "max_input_tokens_per_request": 600}
    )
    assert estimate_api_equivalent_cost(normalize(), limited, model=MODEL)["status"] == "unknown"
    assert (
        estimate_api_equivalent_cost(
            normalize(receipts=[receipt(1, 400, 20), receipt(2, 600, 30)]),
            limited,
            model=MODEL,
        )["status"]
        == "estimated"
    )
    assert (
        estimate_api_equivalent_cost(
            normalize(receipts=[receipt()]),
            limited,
            model=MODEL,
        )["status"]
        == "unknown"
    )


def test_cache_write_and_reasoning_are_not_silently_double_charged():
    events = transcript()
    events[-1]["usage"].update(reasoning_output_tokens=20, cache_write_input_tokens=0)
    result = estimate_api_equivalent_cost(normalize(events), schedule(), model=MODEL)
    assert result["estimated_api_equivalent_usd"] == "0.0021"
    events[-1]["usage"]["cache_write_input_tokens"] = 20
    result = estimate_api_equivalent_cost(normalize(events), schedule(), model=MODEL)
    assert result["status"] == "unknown"
    assert result["cash_cost_usd"] is None


def test_post_terminal_events_and_prebaseline_identity_do_not_get_costed():
    events = [*transcript(), {"type": "item.completed", "item": {"type": "agent_message"}}]
    assert normalize(events)["status"] == "invalid"
    assert (
        normalize_codex_usage(
            transcript(),
            codex_identity={"observed_version": "0.163.0"},
            model=MODEL,
        )["status"]
        == "unknown"
    )


def test_cost_revalidates_partition_instead_of_trusting_complete_label():
    usage = normalize()
    usage["uncached_input_tokens"] = 1000
    result = estimate_api_equivalent_cost(usage, schedule(), model=MODEL)
    assert result["status"] == "unknown"
    assert result["estimated_api_equivalent_usd"] is None


def test_provider_identity_type_variation_cannot_double_count_one_request():
    assert normalize(receipts=[receipt(1, 500, 25), receipt("1", 500, 25)])["status"] == "invalid"
