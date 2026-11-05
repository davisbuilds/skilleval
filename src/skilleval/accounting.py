"""Offline native usage observations and conditional flat-rate cost scenarios.

The source reference describes a reviewed upstream checkout, not a claim that
an observed binary was built from it. These functions neither call providers nor
calibrate native telemetry. No price schedule or observed cash charge is assumed.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal, localcontext
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator

from skilleval.contracts import Record
from skilleval.errors import SkillEvalError
from skilleval.harnesses.codex import codex_version_identity

CODEX_USAGE_SOURCE_REVISION = "1c0c45bfc372fb7d7d581508fdef5eef8235eab7"
CODEX_USAGE_SOURCE = (
    "https://github.com/openai/codex/blob/"
    + CODEX_USAGE_SOURCE_REVISION
    + "/codex-rs/exec/src/event_processor_with_jsonl_output.rs#L118-L128"
)
CODEX_APP_SERVER_USAGE_SOURCE_REVISION = "6aaac694803004a63c6f393d153c96d22521a6b9"
CODEX_APP_SERVER_USAGE_SOURCE = (
    "https://github.com/openai/codex/blob/"
    + CODEX_APP_SERVER_USAGE_SOURCE_REVISION
    + "/codex-rs/app-server/src/bespoke_event_handling.rs#L1602-L1615"
)
_COUNTS = ("input_tokens", "cached_input_tokens", "output_tokens")
_USAGE_FIELDS = {*_COUNTS, "cache_write_input_tokens", "reasoning_output_tokens", "total_tokens"}


class PriceSchedule(Record):
    """Caller-supplied public rates; validating this record does not verify them."""

    model: str = Field(min_length=1, max_length=128)
    currency: Literal["USD"] = "USD"
    source_url: str = Field(max_length=2048)
    retrieved_on: date
    input_per_million: Decimal = Field(ge=0, allow_inf_nan=False, max_digits=32, decimal_places=16)
    cached_input_per_million: Decimal = Field(
        ge=0, allow_inf_nan=False, max_digits=32, decimal_places=16
    )
    output_per_million: Decimal = Field(ge=0, allow_inf_nan=False, max_digits=32, decimal_places=16)
    rate_scope: str = Field(
        default="caller-supplied-flat-rate-scenario", min_length=1, max_length=512
    )
    service_tier: Literal["standard"] = "standard"
    max_input_tokens_per_request: int | None = Field(default=None, ge=1)
    cache_write_policy: Literal["excluded"] = "excluded"
    assumptions: tuple[str, ...] = ()

    @field_validator("source_url")
    @classmethod
    def public_source(cls, value: str) -> str:
        try:
            url = urlsplit(value)
            hostname = url.hostname
            if (
                url.scheme != "https"
                or hostname is None
                or url.username is not None
                or url.password is not None
                or url.port not in {None, 443}
                or "." not in hostname
                or hostname.endswith((".localhost", ".local", ".internal"))
            ):
                raise ValueError("source_url must be a public HTTPS source")
            try:
                address = ipaddress.ip_address(hostname)
            except ValueError:
                pass
            else:
                if not address.is_global:
                    raise ValueError("source_url must be public")
        except ValueError as error:
            raise ValueError("source_url must be a public HTTPS source") from error
        return value

    @field_validator("model", "rate_scope")
    @classmethod
    def no_padding(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("model and scope must not contain surrounding whitespace")
        return value


def _count(value: Any) -> bool:
    return type(value) is int and 0 <= value <= 2**63 - 1


def _reconcile(
    receipts: Sequence[Mapping[str, Any]] | None, model: str, native: Mapping[str, int]
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "unknown",
        "source": "host-broker-provider-response-usage",
        "request_count": None,
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "max_input_tokens_per_request": None,
        "cached_input_status": "unknown",
        "reasons": [],
    }
    if receipts is None:
        result["reasons"] = ["independent provider observations not supplied"]
        return result
    result["request_count"] = len(receipts)
    if not receipts:
        result.update(status="incomplete", reasons=["no provider request observations"])
        return result
    seen: set[str] = set()
    sums = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    maximum = 0
    cached = 0
    cached_complete = True
    for row in receipts:
        request_id = row.get("request_id")
        if not (
            (type(request_id) is int and request_id > 0)
            or (isinstance(request_id, str) and bool(request_id.strip()))
        ):
            result.update(status="invalid", reasons=["invalid provider request identity"])
            return result
        if str(request_id) in seen:
            result.update(status="invalid", reasons=["duplicate provider request identity"])
            return result
        seen.add(str(request_id))
        if row.get("event") != "request" or row.get("model") != model:
            result.update(status="invalid", reasons=["provider event or model mismatch"])
            return result
        if row.get("outcome") != "complete" or row.get("upstream_status") != 200:
            result.update(status="incomplete", reasons=["provider request did not complete"])
            return result
        usage = row.get("usage")
        if not isinstance(usage, Mapping) or not all(key in usage for key in sums):
            result.update(status="incomplete", reasons=["provider usage fields missing"])
            return result
        if not all(_count(usage[key]) for key in sums):
            result.update(status="invalid", reasons=["invalid provider token counts"])
            return result
        if usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]:
            result.update(
                status="invalid", reasons=["provider total does not equal input plus output"]
            )
            return result
        for key in sums:
            sums[key] += usage[key]
        maximum = max(maximum, usage["input_tokens"])
        details = usage.get("input_tokens_details")
        if isinstance(details, Mapping) and "cached_tokens" in details:
            observed = details["cached_tokens"]
            if not _count(observed) or observed > usage["input_tokens"]:
                result.update(status="invalid", reasons=["invalid provider cached token count"])
                return result
            cached += observed
        else:
            cached_complete = False
    result.update(sums, max_input_tokens_per_request=maximum)
    if any(sums[key] != native[key] for key in sums):
        result.update(
            status="mismatch", reasons=["native totals disagree with provider observations"]
        )
    elif cached_complete and cached != native["cached_input_tokens"]:
        result.update(
            status="mismatch",
            cached_input_status="mismatch",
            reasons=["native cache count disagrees with provider observations"],
        )
    else:
        result.update(
            status="matched", cached_input_status="matched" if cached_complete else "unknown"
        )
        if not cached_complete:
            result["reasons"] = [
                "provider cache detail absent; native cache partition not cross-checked"
            ]
    return result


def normalize_codex_usage(
    events: Sequence[Mapping[str, Any]],
    *,
    codex_identity: Mapping[str, Any],
    model: str,
    provider_observations: Sequence[Mapping[str, Any]] | None = None,
    parse_errors: Sequence[Mapping[str, Any]] | None = None,
    surface: str = "codex-exec-json",
) -> dict[str, Any]:
    """Normalize one fresh, fully captured canonical turn; never sum snapshots.

    Provider observations, when supplied, must be the full host broker request
    ledger for this invocation. This function can check its rows, not independently
    establish that the caller supplied an exhaustive ledger or a fresh thread.
    The surface selects provenance, not parsing: app-server callers supply the
    adapter's canonical events and parse errors while retaining the raw RPC stream.
    """
    if surface not in {"codex-exec-json", "codex-app-server-jsonrpc-v2"}:
        raise SkillEvalError("unsupported Codex usage surface")
    result: dict[str, Any] = {
        "schema_version": 1,
        "status": "unknown",
        "model": model,
        **dict.fromkeys((*_COUNTS, "uncached_input_tokens", "total_tokens")),
        "cache_write_input_tokens": None,
        "reasoning_output_tokens": None,
        "reasons": [],
        "provenance": {
            "surface": "codex-exec-json",
            "record": "turn.completed.usage",
            "form": "single terminal cumulative thread snapshot",
            "codex_identity": dict(codex_identity),
            "source_revision": CODEX_USAGE_SOURCE_REVISION,
            "source_url": CODEX_USAGE_SOURCE,
            "source_binary_equivalence": "unverified",
            "measurement": "native-reported; not calibrated provider billing",
            "terminal_event_index": None,
        },
        "provider_reconciliation": _reconcile(None, model, {}),
    }
    if surface == "codex-app-server-jsonrpc-v2":
        result["provenance"].update(
            surface=surface,
            record="thread/tokenUsage/updated.params.tokenUsage.total",
            form="latest matched-thread cumulative snapshot before matched turn/completed",
            adapter="app-server-v2-to-canonical-v1",
            source_revision=CODEX_APP_SERVER_USAGE_SOURCE_REVISION,
            source_url=CODEX_APP_SERVER_USAGE_SOURCE,
        )

    def stop(status: str, reason: str) -> dict[str, Any]:
        result.update(status=status, reasons=[reason])
        return result

    if parse_errors:
        return stop("incomplete", "event stream has parse errors")
    if not events:
        return stop("unknown", "native event transcript absent")
    if any(not isinstance(event.get("type"), str) for event in events):
        return stop("invalid", "malformed native event record")
    threads = [index for index, event in enumerate(events) if event["type"] == "thread.started"]
    starts = [index for index, event in enumerate(events) if event["type"] == "turn.started"]
    terminals = [index for index, event in enumerate(events) if event["type"] == "turn.completed"]
    if len(threads) > 1 or len(starts) > 1 or len(terminals) > 1:
        return stop(
            "invalid", "multiple turns or threads unsupported; cumulative usage is not summed"
        )
    if any(event["type"] in {"turn.failed", "error"} for event in events):
        return stop("incomplete", "native stream contains failure evidence")
    if not terminals:
        return stop("incomplete", "completed native terminal event absent")
    terminal = terminals[0]
    result["provenance"]["terminal_event_index"] = terminal
    if terminal != len(events) - 1:
        return stop("invalid", "native events follow the terminal usage snapshot")
    if len(threads) != 1 or len(starts) != 1:
        return stop("incomplete", "full thread and turn start evidence required")
    if not (threads[0] == 0 < starts[0] < terminal):
        return stop("invalid", "native start and terminal event ordering invalid")
    if not isinstance(events[0].get("thread_id"), str) or not events[0]["thread_id"].strip():
        return stop("invalid", "native thread identity missing or invalid")
    version = codex_identity.get("observed_version")
    if not isinstance(version, str):
        return stop("unknown", "observed admitted Codex version identity unavailable")
    try:
        codex_version_identity("codex-cli " + version)
    except SkillEvalError:
        return stop("unknown", "observed Codex version is outside the admitted adapter policy")
    if not model or model != model.strip():
        return stop("invalid", "model identity required")
    raw = events[terminal].get("usage")
    if not isinstance(raw, Mapping) or any(key not in raw for key in _COUNTS):
        return stop("incomplete", "native usage fields missing")
    if set(raw) - _USAGE_FIELDS:
        return stop("invalid", "unreviewed native usage fields")
    if not all(_count(value) for value in raw.values()):
        return stop("invalid", "native token counts must be nonnegative signed-64-bit integers")
    native = {key: raw[key] for key in _COUNTS}
    native["total_tokens"] = native["input_tokens"] + native["output_tokens"]
    if not _count(native["total_tokens"]):
        return stop("invalid", "native aggregate total exceeds signed-64-bit token range")
    if native["cached_input_tokens"] > native["input_tokens"]:
        return stop("invalid", "native cached input exceeds total input")
    if "total_tokens" in raw and raw["total_tokens"] != native["total_tokens"]:
        return stop("invalid", "native total does not equal input plus output")
    if raw.get("reasoning_output_tokens", 0) > native["output_tokens"]:
        return stop("invalid", "native reasoning output exceeds total output")
    if raw.get("cache_write_input_tokens", 0) > native["input_tokens"]:
        return stop("invalid", "native cache write input exceeds total input")
    reconciliation = _reconcile(provider_observations, model, native)
    result["provider_reconciliation"] = reconciliation
    if reconciliation["status"] in {"invalid", "mismatch"}:
        return stop("invalid", "independent provider reconciliation failed")
    if reconciliation["status"] == "incomplete":
        return stop("incomplete", "independent provider observations incomplete")
    if not native["total_tokens"] and reconciliation["status"] != "matched":
        return stop("unknown", "native zero snapshot may be missing-telemetry fallback")
    result.update(
        native,
        status="complete",
        uncached_input_tokens=(native["input_tokens"] - native["cached_input_tokens"]),
        cache_write_input_tokens=raw.get("cache_write_input_tokens"),
        reasoning_output_tokens=raw.get("reasoning_output_tokens"),
    )
    return result


def estimate_api_equivalent_cost(
    usage: Mapping[str, Any], schedule: PriceSchedule | None, *, model: str
) -> dict[str, Any]:
    """Compute a caller-selected flat-rate scenario, never an observed charge."""
    result: dict[str, Any] = {
        "schema_version": 1,
        "status": "unknown",
        "estimated_api_equivalent_usd": None,
        "cash_cost_usd": None,
        "currency": "USD",
        "reasons": [],
        "schedule": schedule.model_dump(mode="json") if schedule is not None else None,
        "meaning": "conditional flat-rate API-equivalent estimate; not an observed charge",
        "exclusions": [
            "tool charges",
            "regional adjustments",
            "subscription billing",
            "unobserved service-tier or context-rate changes",
            "cache-write charges",
        ],
    }
    if schedule is None:
        result["reasons"] = ["no caller-supplied price schedule"]
        return result
    if schedule.model != model or usage.get("model") != model:
        raise ValueError("price schedule and usage must match the exact model identity")
    if usage.get("status") != "complete":
        result["reasons"] = ["complete usage observation required"]
        return result
    if usage.get("cache_write_input_tokens") not in {None, 0}:
        result["reasons"] = ["cache writes require a separately supported price contract"]
        return result
    if schedule.max_input_tokens_per_request is not None:
        provider = usage.get("provider_reconciliation", {})
        maximum = provider.get("max_input_tokens_per_request")
        if provider.get("status") != "matched" or not _count(maximum):
            result["reasons"] = ["complete provider input sizes required for context price scope"]
            return result
        if maximum > schedule.max_input_tokens_per_request:
            result["reasons"] = ["provider request exceeds schedule context price scope"]
            return result
    keys = ("uncached_input_tokens", "cached_input_tokens", "output_tokens")
    if not all(_count(usage.get(key)) for key in keys):
        result["reasons"] = ["complete normalized token counts required"]
        return result
    if (
        not _count(usage.get("input_tokens"))
        or not _count(usage.get("total_tokens"))
        or usage["input_tokens"] != usage["uncached_input_tokens"] + usage["cached_input_tokens"]
        or usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]
    ):
        result["reasons"] = ["normalized token partitions or total inconsistent"]
        return result
    rates = (
        schedule.input_per_million,
        schedule.cached_input_per_million,
        schedule.output_per_million,
    )
    with localcontext() as context:
        context.prec = 100
        cost = sum(
            (Decimal(usage[key]) * rate for key, rate in zip(keys, rates, strict=True)), Decimal(0)
        ) / Decimal(1_000_000)
        rendered = format(cost.normalize(), "f")
    result.update(status="estimated", estimated_api_equivalent_usd=rendered)
    result["reasons"] = [
        "caller asserts flat-rate schedule applicability; excluded charges unknown"
    ]
    return result
