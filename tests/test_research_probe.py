"""Offline qualification controls; these never launch a VM, web request or model."""

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/qualify-research.py"


def module():
    spec = importlib.util.spec_from_file_location("research_probe", SCRIPT)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def events(*values):
    return b"\n".join(json.dumps(value).encode() for value in values)


def completed(kind, **fields):
    return {"type": "item.completed", "item": {"type": kind, "status": "completed", **fields}}


def test_completed_search_and_open_are_detected_independently():
    observed = module().event_observations(
        events(
            completed("web_search", action={"type": "search", "query": "urllib.parse"}),
            completed(
                "web_search", action={"type": "open_page", "url": "https://docs.python.org/"}
            ),
        )
    )
    assert observed["search_completed"] is True
    assert observed["open_completed"] is True
    assert observed["local_marker_completed"] is False


def test_starts_failures_and_citations_do_not_prove_web_completion():
    observed = module().event_observations(
        events(
            {"type": "item.started", "item": {"type": "web_search", "action": {"type": "search"}}},
            completed("web_search", action={"type": "search"}, status="failed"),
            completed("agent_message", text="Opened https://docs.python.org/"),
            completed("web_search", query="urllib.parse", citations=["https://docs.python.org/"]),
        )
    )
    assert observed["search_completed"] is False
    assert observed["open_completed"] is False


def test_local_marker_proves_success_and_order_separately():
    probe = module()
    search = completed("web_search_call", action={"type": "search"})
    opened = completed("web_search_call", action={"type": "open_page"})
    command = completed("command_execution", exit_code=0, aggregated_output=probe.MARKER + "\n")
    assert probe.event_observations(events(search, opened, command))["hosted_then_local_replay"]
    assert not probe.event_observations(events(command, search, opened))["hosted_then_local_replay"]
    command["item"]["exit_code"] = 1
    assert not probe.event_observations(events(search, opened, command))["local_marker_completed"]
    command["item"].update(exit_code=0, aggregated_output="echo " + probe.MARKER)
    assert not probe.event_observations(events(search, opened, command))["local_marker_completed"]


def test_control_inputs_keep_worker_and_expected_labels_outside_solver(tmp_path):
    probe = module()
    spec, selected = probe.prepare_control(tmp_path)
    from skilleval import repair
    from skilleval.runner import _snapshot_files

    restored = repair.load_task(Path(selected["path"]), selected["sha256"])
    assert restored == spec
    _, files = _snapshot_files(Path(spec.input_bundle), spec.input_sha256)
    assert [name for name, _, _ in files] == ["context.md"]
    assert b"not a retained source" in files[0][1]
    assert "native web tool" in spec.prompt
    assert "report.md" in spec.editable_paths
    assert Path(spec.worker).parent == tmp_path
    assert all(item.expected is True for item in spec.probes)


def invoke(*args):
    import subprocess
    import sys

    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)], text=True, capture_output=True, check=False
    )


def arguments(tmp_path):
    return {
        "--runtime": tmp_path / "unavailable-runtime",
        "--image-archive": tmp_path / "unavailable-image.tar",
        "--image-sha256": "0" * 64,
        "--catalog": tmp_path / "unavailable-models.json",
        "--auth-file": tmp_path / "unavailable-auth.json",
        "--output": tmp_path / "evidence",
    }


def flatten(options):
    return [item for pair in options.items() for item in pair]


def test_help_is_read_only_and_does_not_require_credentials(tmp_path):
    result = invoke("--help", "--output", tmp_path / "evidence")
    assert result.returncode == 0, result.stderr
    assert "--auth-file" in result.stdout and "--recover" in result.stdout
    assert not (tmp_path / "evidence").exists()


def test_all_runtime_inputs_are_explicit_before_output_creation(tmp_path):
    for missing in arguments(tmp_path):
        options = arguments(tmp_path)
        del options[missing]
        result = invoke(*flatten(options))
        assert result.returncode == 2, result.stderr
        assert "provide --runtime" in result.stderr
        assert not (tmp_path / "evidence").exists()


def test_existing_evidence_is_not_replaced(tmp_path):
    output = tmp_path / "evidence"
    output.mkdir()
    original = output / "qualification.json"
    original.write_bytes(b"retained failed attempt\n")
    result = invoke(*flatten(arguments(tmp_path)))
    assert result.returncode == 2
    assert "fresh directory" in result.stderr
    assert original.read_bytes() == b"retained failed attempt\n"
    assert list(output.iterdir()) == [original]


def test_pins_and_recovery_are_checked_before_output_creation(tmp_path):
    for digest in ("", "A" * 64, "z" * 64, "0" * 63):
        result = invoke(*flatten(arguments(tmp_path) | {"--image-sha256": digest}))
        assert result.returncode == 2
        assert not (tmp_path / "evidence").exists()
    result = invoke("--recover", tmp_path / "ticket", *flatten(arguments(tmp_path)))
    assert result.returncode == 2
    assert "cannot be combined" in result.stderr
    assert not (tmp_path / "evidence").exists()


def test_research_evidence_cannot_be_written_inside_checkout(tmp_path):
    options = arguments(tmp_path) | {"--output": SCRIPT.parent / "uncreated-research-control"}
    result = invoke(*flatten(options))
    assert result.returncode == 2
    assert "outside this checkout" in result.stderr
    assert not options["--output"].exists()


def test_native_open_page_action_variant_is_explicitly_observed():
    observed = module().event_observations(
        events(
            completed("web_search", action={"type": "openPage", "url": "https://docs.python.org/"}),
        )
    )
    assert observed["open_completed"] is True
    assert observed["search_completed"] is False
