"""Mode budgets across real local dispatch, frozen plans, and auth freshness."""

import hashlib
import json
import tarfile
import time
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from test_gateway import Stream, request, write_auth
from test_gateway import short_root as short_root
from test_runner import invoke
from test_runner import route as route
from test_study import prepared as prepared

from skilleval import cli, gateway, runner, study
from skilleval.errors import SkillEvalError
from skilleval.lifecycle import Lifecycle


@pytest.mark.parametrize("mode,deadline", [("review", 1800), ("repair", 1800), ("judge", 900)])
def test_native_omitted_deadline_reaches_guest_and_supervisor(
    tmp_path: Path,
    route: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    deadline: int,
) -> None:
    delivered = {}
    original = Lifecycle.guest

    def inspect_guest(session: Lifecycle, label: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if label == "native_task":
            with tarfile.open(Path(session.ticket["runtime_state"]) / "tooling.tar") as archive:
                member = archive.extractfile("native/settings.json")
                assert member is not None
                delivered.update(json.load(member))
        return original(session, label, *args, **kwargs)

    monkeypatch.setattr(Lifecycle, "guest", inspect_guest)
    route.update(mode=mode, investigation=False if mode == "judge" else None)
    report = invoke(tmp_path, route)
    assert report["status"] == "completed", report.get("error")
    assert report["native_timeout_seconds"] == deadline
    assert report["gateway_limits"]["lifetime_seconds"] == deadline + 1020
    assert report["gateway_limits"]["max_requests"] == 32
    assert report["cleanup_complete"] and report["observed_absent"]
    # Inspect the exact archive delivered to the synthetic runtime, plus the
    # actual command persisted by the lifecycle supervisor.
    assert delivered["native_timeout_seconds"] == deadline
    ticket = json.loads((tmp_path / "run/recovery.json").read_text())
    step = next(s for s in ticket["steps"] if s["label"] == "native_task")
    assert step["argv"][step["argv"].index("--timeout") + 1] == f"{deadline + 30}s"


@pytest.mark.parametrize("mode", ["review", "repair", "judge"])
@pytest.mark.parametrize("explicit_limits", [False, True])
def test_explicit_short_deadline_and_minimum_gateway_headroom(
    tmp_path: Path,
    route: dict[str, Any],
    mode: str,
    explicit_limits: bool,
) -> None:
    route.update(
        mode=mode,
        investigation=False if mode == "judge" else None,
        native_timeout_seconds=17,
    )
    if explicit_limits:
        route["limits"] = gateway.GatewayLimits(
            max_requests=7,
            timeout_seconds=2,
            lifetime_seconds=137,
        )
    report = invoke(tmp_path, route)
    assert report["status"] == "completed", report.get("error")
    assert report["native_timeout_seconds"] == 17
    assert report["gateway_limits"]["lifetime_seconds"] == (137 if explicit_limits else 1037)
    assert report["gateway_limits"]["max_requests"] == (7 if explicit_limits else 32)


@pytest.mark.parametrize(
    "mode,deadline,lifetime",
    [
        ("review", 1801, 1920),
        ("repair", 1801, 1920),
        ("judge", 901, 1920),
        ("review", True, 1920),
        ("judge", 0, 1920),
        ("repair", 17, 76),
    ],
)
def test_invalid_native_budgets_refuse_before_input_or_auth_access(
    tmp_path: Path, mode: str, deadline: int, lifetime: int
) -> None:
    with pytest.raises(SkillEvalError, match="deadline"):
        runner.run_native(
            tmp_path / "missing",
            "0" * 64,
            "Inspect",
            tmp_path / "output",
            runtime=tmp_path / "runtime",
            image=tmp_path / "image",
            image_sha256="0" * 64,
            catalog=tmp_path / "catalog",
            auth_file=tmp_path / "auth",
            mode=mode,
            investigation=False if mode == "judge" else None,
            native_timeout_seconds=deadline,
            limits=gateway.GatewayLimits(lifetime_seconds=lifetime),
        )
    assert not (tmp_path / "output").exists()


def test_gateway_lifetime_ceiling_covers_task_and_no_more() -> None:
    assert gateway.GatewayLimits(lifetime_seconds=2820).lifetime_seconds == 2820
    with pytest.raises(SkillEvalError, match="deadline"):
        gateway.GatewayLimits(lifetime_seconds=2821)


@pytest.mark.parametrize("mode,remaining", [("review", 2840), ("judge", 1940)])
def test_native_auth_must_cover_full_derived_lifetime_before_output(
    tmp_path: Path,
    route: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    remaining: int,
) -> None:
    monkeypatch.setattr(runner, "load_chatgpt_auth", gateway.load_chatgpt_auth)
    route.update(mode=mode, investigation=False if mode == "judge" else None)
    route["auth_file"] = write_auth(tmp_path, time.time() + remaining)
    with pytest.raises(SkillEvalError, match="unavailable or expired"):
        invoke(tmp_path, route)
    assert not (tmp_path / "run").exists()


def test_sufficient_auth_covers_full_task_lifetime(
    tmp_path: Path,
    route: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "load_chatgpt_auth", gateway.load_chatgpt_auth)
    route["auth_file"] = write_auth(tmp_path, time.time() + 3000)
    report = invoke(tmp_path, route)
    assert report["status"] == "completed", report.get("error")


@pytest.mark.parametrize("mode,deadline", [("review", 1800), ("repair", 1800), ("judge", 900)])
@pytest.mark.parametrize("override", [None, 23])
def test_cli_selects_mode_budget_and_preserves_request_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    deadline: int,
    override: int | None,
) -> None:
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("Inspect")
    captured: dict[str, Any] = {}

    def capture(*args: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"status": "completed"}

    monkeypatch.setattr(runner, "run_native", capture)
    argv = [
        "native",
        "qualify",
        "bundle",
        "--sha256",
        "0" * 64,
        "--prompt",
        str(prompt),
        "--mode",
        mode,
        "--runtime",
        "runtime",
        "--image-archive",
        "image",
        "--image-sha256",
        "0" * 64,
        "--catalog",
        "catalog",
        "--auth-file",
        "auth",
        "--output",
        "output",
        "--max-requests",
        "7",
    ]
    if mode == "judge":
        argv.extend(["--judge-policy", "static"])
    if override is not None:
        argv.extend(["--timeout-seconds", str(override)])
    cli.dispatch(cli.parser().parse_args(argv))
    selected = deadline if override is None else override
    assert captured["native_timeout_seconds"] == selected
    assert captured["limits"].lifetime_seconds == selected + 1020
    assert captured["limits"].max_requests == 7


def test_new_study_freezes_and_dispatches_30_minute_default(prepared: dict[str, Any]) -> None:
    plan = json.loads(prepared["plan_path"].read_bytes())
    assert plan["spec"]["native_timeout_seconds"] == 1800
    result = study.run_study(**prepared, development=True)
    assert result["status"] == "complete"
    for cell in result["cells"]:
        native = Path(cell["attempts"][0]["path"]) / "native/native-run.json"
        receipt = json.loads(native.read_bytes())
        assert receipt["native_timeout_seconds"] == 1800
        assert receipt["gateway_limits"]["lifetime_seconds"] == 2820


@pytest.mark.parametrize("schema", [1, 2, 3])
@pytest.mark.parametrize("recorded", [None, 600, 780])
def test_old_plan_readback_keeps_historic_budget_and_bytes(
    prepared: dict[str, Any],
    schema: int,
    recorded: int | None,
) -> None:
    path = prepared["plan_path"]
    plan = json.loads(path.read_bytes())
    plan["schema_version"] = schema
    if recorded is None:
        plan["spec"].pop("native_timeout_seconds")
    else:
        plan["spec"]["native_timeout_seconds"] = recorded
    if schema < 3:
        for task in plan["spec"]["tasks"]:
            task.pop("mode")
    if schema == 1:
        for key in ("invocation_policy", "price_schedule"):
            plan["spec"].pop(key)
        for key in ("invocations", "skill_name"):
            plan.pop(key)
    raw = json.dumps(plan).encode()
    path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    value, spec = study._plan(path, digest)
    assert spec.native_timeout_seconds == (600 if recorded is None else recorded)
    assert value == plan and path.read_bytes() == raw
    if schema == 3 and recorded is None:
        prepared["expected_sha256"] = digest
        result = study.run_study(**prepared, development=True)
        assert result["status"] == "complete"
        for cell in result["cells"]:
            native = Path(cell["attempts"][0]["path"]) / "native/native-run.json"
            assert json.loads(native.read_bytes())["native_timeout_seconds"] == 600
        assert study.run_study(**prepared, development=True) == result
        assert path.read_bytes() == raw


def test_study_accepts_ceiling_and_rejects_larger_deadline(prepared: dict[str, Any]) -> None:
    data = json.loads(prepared["plan_path"].read_bytes())["spec"]
    assert study.StudySpec.model_validate_json(json.dumps({**data, "native_timeout_seconds": 1800}))
    with pytest.raises(ValidationError):
        study.StudySpec.model_validate_json(json.dumps({**data, "native_timeout_seconds": 1801}))


def test_long_gateway_still_rechecks_auth_before_each_request(
    short_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = time.time()
    auth = gateway.load_chatgpt_auth(
        write_auth(short_root, initial + 3000), minimum_validity_seconds=2850
    )
    sent: list[bytes] = []

    def transport(raw: bytes, *_args: Any) -> Stream:
        sent.append(raw)
        return Stream()

    with gateway.broker_scope(
        auth,
        short_root / "long-budget",
        limits=gateway.GatewayLimits(timeout_seconds=90, lifetime_seconds=2820),
        test_transport=transport,
    ) as broker:
        assert request(broker.socket_path)[0] == 200
        # Still inside the gateway lifetime, but insufficient validity for one
        # more request plus its expiry guard. No refresh or provider dispatch.
        monkeypatch.setattr(gateway.time, "time", lambda: initial + 2906)
        status, raw = request(broker.socket_path)
        assert status == 503 and b"authentication_expired" in raw
        assert len(sent) == 1


@pytest.mark.parametrize("setup_seconds,allowed", [(180, True), (961, False)])
def test_delayed_setup_preserves_full_default_native_deadline_or_refuses(
    tmp_path: Path,
    route: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    setup_seconds: int,
    allowed: bool,
) -> None:
    from types import SimpleNamespace

    elapsed = 0
    native_calls = []
    original = Lifecycle.guest
    clock = time.monotonic
    monkeypatch.setattr(
        runner,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock() + elapsed,
            time=time.time,
        ),
    )

    def delayed_prepare(
        session: Lifecycle, label: str, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal elapsed
        if label == "native_task":
            native_calls.append(label)
        result = original(session, label, *args, **kwargs)
        if label == "prepare_task":
            elapsed += setup_seconds
        return result

    monkeypatch.setattr(Lifecycle, "guest", delayed_prepare)
    report = invoke(tmp_path, route)
    assert (report["status"] == "completed") is allowed
    assert bool(native_calls) is allowed
    assert report["cleanup_complete"] and report["observed_absent"]
    if allowed:
        assert report["gateway_limits"]["lifetime_seconds"] >= setup_seconds + 1800 + 60
        assert report["gateway_budget"]["status"] == "ready"
        assert report["gateway_budget"]["remaining_seconds"] >= 1860
    else:
        assert report["status"] == "partial"
        assert report["gateway_budget"]["status"] == "insufficient-time"
        assert "gateway lifetime exhausted during setup" in report["error"]
        assert not (tmp_path / "run/events.jsonl").exists()


@pytest.mark.parametrize("setup_seconds,allowed", [(230, True), (250, False)])
def test_explicit_gateway_lifetime_is_never_silently_shortened_or_extended(
    tmp_path: Path,
    route: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    setup_seconds: int,
    allowed: bool,
) -> None:
    from types import SimpleNamespace

    elapsed = 0
    original = Lifecycle.guest
    clock = time.monotonic
    monkeypatch.setattr(
        runner,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock() + elapsed,
            time=time.time,
        ),
    )

    def delayed_prepare(
        session: Lifecycle, label: str, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal elapsed
        result = original(session, label, *args, **kwargs)
        if label == "prepare_task":
            elapsed += setup_seconds
        return result

    monkeypatch.setattr(Lifecycle, "guest", delayed_prepare)
    route.update(
        native_timeout_seconds=600,
        limits=gateway.GatewayLimits(timeout_seconds=90, lifetime_seconds=900),
    )
    report = invoke(tmp_path, route)
    assert (report["status"] == "completed") is allowed
    assert report["gateway_limits"]["lifetime_seconds"] == 900
    assert report["cleanup_complete"] and report["observed_absent"]
    assert report["gateway_budget"]["status"] == ("ready" if allowed else "insufficient-time")


def test_host_preparation_rechecks_auth_before_gateway_and_vm_mutation(
    tmp_path: Path,
    route: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    wall_offset = 0
    clock = time.time
    monkeypatch.setattr(
        runner,
        "time",
        SimpleNamespace(
            monotonic=time.monotonic,
            time=lambda: clock() + wall_offset,
        ),
    )
    monkeypatch.setattr(runner, "load_chatgpt_auth", gateway.load_chatgpt_auth)
    route["auth_file"] = write_auth(tmp_path, clock() + 2870)
    original = Lifecycle.prepare.__func__

    def delayed_prepare(cls: type[Lifecycle], *args: Any, **kwargs: Any) -> Lifecycle:
        nonlocal wall_offset
        session = original(cls, *args, **kwargs)
        wall_offset = 100
        return session

    monkeypatch.setattr(Lifecycle, "prepare", classmethod(delayed_prepare))
    report = invoke(tmp_path, route)
    assert report["status"] == "partial"
    assert "authentication unavailable or expired" in report["error"]
    ticket = json.loads((tmp_path / "run/recovery.json").read_text())
    assert not ticket["create_attempted"] and report["cleanup_complete"]


def test_wall_clock_jump_during_setup_rechecks_auth_before_native_work(
    tmp_path: Path,
    route: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    wall_offset = 0
    clock = time.time
    monkeypatch.setattr(
        runner,
        "time",
        SimpleNamespace(
            monotonic=time.monotonic,
            time=lambda: clock() + wall_offset,
        ),
    )
    monkeypatch.setattr(runner, "load_chatgpt_auth", gateway.load_chatgpt_auth)
    route["auth_file"] = write_auth(tmp_path, clock() + 3000)
    original = Lifecycle.guest
    native_calls = []

    def delayed_prepare(
        session: Lifecycle, label: str, *args: Any, **kwargs: Any
    ) -> dict[str, Any]:
        nonlocal wall_offset
        if label == "native_task":
            native_calls.append(label)
        result = original(session, label, *args, **kwargs)
        if label == "prepare_task":
            wall_offset = 1200
        return result

    monkeypatch.setattr(Lifecycle, "guest", delayed_prepare)
    report = invoke(tmp_path, route)
    assert report["status"] == "partial"
    assert "authentication unavailable or expired" in report["error"]
    assert not native_calls and report["cleanup_complete"] and report["observed_absent"]
