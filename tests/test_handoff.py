"""Real retained bytes and filesystem contracts; no models or actual VMs."""

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from test_runner import route as route

from skilleval import evidence, handoff, repair, runner
from skilleval.errors import SkillEvalError
from skilleval.identity import file_digest

SPEC_PATH = "docs/specs/feature.md"
SPEC_BYTES = b"# Export archived records\r\n\r\nInclude all completed records.\r\n"


@pytest.fixture
def writer_factory(tmp_path: Path, route: dict[str, Any]):
    def make(*, files=None, fault=""):
        root = tmp_path / ("writer-" + str(len(list(tmp_path.glob("writer-*")))))
        root.mkdir()
        return _writer(root, route, files=files, fault=fault)

    return make


@pytest.fixture
def writer(writer_factory):
    return writer_factory()


def _writer(tmp_path: Path, route: dict[str, Any], *, files=None, fault="") -> dict[str, Any]:
    source = tmp_path / "source"
    source.mkdir()
    (source / ".git").mkdir()
    (source / ".git/HEAD").write_text("ref: refs/heads/main\n")
    (source / "AGENTS.md").write_text("Ordinary repository instructions.\n")
    (source / "code.py").write_text("raise RuntimeError('import only inside VM')\n")
    (source / "scenario.json").write_text(
        json.dumps(
            {
                "fault": fault,
                "complete_usage": True,
                "repair_files": files
                if files is not None
                else {SPEC_PATH: SPEC_BYTES.decode(), "code.py": "writer change\n"},
            }
        )
    )
    baseline = tmp_path / "baseline"
    captured = evidence.snapshot(source, baseline)
    worker = tmp_path / "worker.py"
    worker.write_text("raise RuntimeError('VM-only observation worker')\n")
    task = repair.RepairSpec(
        task_id="writer-spec",
        input_bundle=str(baseline),
        input_sha256=captured["manifest_sha256"],
        prompt="Write a specification for exporting completed records.",
        editable_paths=(SPEC_PATH, "code.py"),
        editable_directories=("docs/specs",),
        worker=str(worker),
        worker_sha256=file_digest(worker),
        probes=(repair.RepairProbe(probe_id="artifact", request={}, expected=True),),
        source_url="https://example.invalid/source",
        source_revision="pinned-source",
    )
    task_path = tmp_path / "writer-task.json"
    prepared = repair.prepare_repair(task, task_path)
    native = tmp_path / "writer-native"
    runner.run_native(baseline, task.input_sha256, task.prompt, native, mode="repair", **route)
    result = tmp_path / "writer-result"
    collected = repair.collect_repair(task_path, prepared["sha256"], native, result)
    selected = handoff.SpecHandoffSpec(
        task=str(task_path),
        task_sha256=prepared["sha256"],
        result=str(result),
        result_sha256=collected["sha256"],
        spec_path=SPEC_PATH,
        spec_sha256=hashlib.sha256(SPEC_BYTES).hexdigest(),
    )
    return {
        "spec": selected,
        "task": task,
        "native": native,
        "result": result,
        "route": route,
        "source": source,
    }


def test_handoff_starts_from_original_baseline_and_only_selected_artifact(writer, tmp_path):
    output = tmp_path / "handoff"
    handoff.prepare_handoff(writer["spec"], output)
    assert (output / "input/code.py").read_text() == "raise RuntimeError('import only inside VM')\n"
    assert (output / "input" / SPEC_PATH).read_bytes() == SPEC_BYTES
    assert (output / "input/AGENTS.md").read_text() == "Ordinary repository instructions.\n"


def test_roundtrip_binds_lineage_and_uses_neutral_implementation_prompt(writer, tmp_path):
    output = tmp_path / "handoff"
    prepared = handoff.prepare_handoff(writer["spec"], output)
    value = handoff.load_handoff(output, prepared["sha256"])
    assert value["lineage"]["task_id"] == "writer-spec"
    assert value["lineage"]["writer_native_receipt_sha256"] == file_digest(
        writer["native"] / "native-run.json"
    )
    assert value["lineage"]["original_input_sha256"] == writer["task"].input_sha256
    assert value["lineage"]["spec_sha256"] == hashlib.sha256(SPEC_BYTES).hexdigest()
    assert value["semantic_acceptance"] == "unassessed"
    assert prepared["prompt"].startswith(
        "Implement the feature described in the specification at docs/specs/feature.md."
    )
    assert prepared["prompt"].endswith(writer["task"].prompt)
    assert (
        "Original specification-writing request (context; your task is to implement"
        in (prepared["prompt"])
    )
    assert prepared["implementation_skill_policy"] == "absent"
    manifest = evidence.verify(output / "input", prepared["input_digest"])
    assert manifest["meta"] == {}
    assert {row["path"] for row in manifest["files"]} == {
        ".git/HEAD",
        "AGENTS.md",
        "code.py",
        "scenario.json",
        SPEC_PATH,
    }
    assert not any(name in prepared["prompt"] for name in ("native", "available", "absent"))


def test_question_only_final_text_is_not_a_selected_spec(writer_factory, tmp_path, route):
    runtime = route["runtime"]
    runtime.write_text(
        runtime.read_text().replace("Final synthetic answer", "Which records should export?")
    )
    attempt = writer_factory(files={})
    # The native final answer is retained but no specification file was produced.
    assert (attempt["native"] / "final.txt").read_text() == "Which records should export?"
    with pytest.raises(SkillEvalError, match="artifact changed"):
        handoff.prepare_handoff(attempt["spec"], tmp_path / "handoff")
    assert (attempt["native"] / "final.txt").exists()
    assert not (tmp_path / "handoff").exists()


@pytest.mark.parametrize("raw", ["", "   \n\t"])
def test_empty_markdown_is_not_a_spec_artifact(writer_factory, tmp_path, raw):
    attempt = writer_factory(files={SPEC_PATH: raw})
    selected = attempt["spec"].model_copy(
        update={"spec_sha256": hashlib.sha256(raw.encode()).hexdigest()}
    )
    with pytest.raises(SkillEvalError, match="nonempty UTF-8"):
        handoff.prepare_handoff(selected, tmp_path / "handoff")


@pytest.mark.parametrize(
    "name", ["final.txt", "docs/feature.md", "docs/specs/x.txt", "docs/specs/../x.md"]
)
def test_selection_requires_explicit_markdown_in_allowed_spec_directory(writer, name):
    value = {**writer["spec"].model_dump(mode="json"), "spec_path": name}
    with pytest.raises((ValueError, SkillEvalError), match=r"docs/specs|unsafe"):
        handoff.SpecHandoffSpec.model_validate_json(json.dumps(value))


def test_selection_requires_the_explicit_artifact_digest(writer, tmp_path):
    selected = writer["spec"].model_copy(update={"spec_sha256": "0" * 64})
    with pytest.raises(SkillEvalError, match="artifact digest"):
        handoff.prepare_handoff(selected, tmp_path / "handoff")


def test_failed_writer_artifact_remains_inspectable_without_claiming_success(
    writer_factory, tmp_path
):
    attempt = writer_factory(fault="timed-out")
    assert (attempt["native"] / "final.txt").read_bytes() == b""
    output = tmp_path / "handoff"
    prepared = handoff.prepare_handoff(attempt["spec"], output)
    value = handoff.load_handoff(output, prepared["sha256"])
    assert value["lineage"]["writer_execution_status"] == "timed-out"
    assert value["semantic_acceptance"] == "unassessed"
    assert (output / "input" / SPEC_PATH).read_bytes() == SPEC_BYTES
    native = json.loads((attempt["native"] / "native-run.json").read_text())
    assert native["status"] == "timed-out"
    assert native["usage_accounting"]["status"] != "complete"


def test_control_with_borrowed_native_provenance_is_rejected(writer, tmp_path):
    candidate_source = tmp_path / "forged-source"
    candidate_source.mkdir()
    for name in ("AGENTS.md", "code.py", "scenario.json"):
        (candidate_source / name).write_bytes((writer["source"] / name).read_bytes())
    selected = candidate_source / SPEC_PATH
    selected.parent.mkdir(parents=True)
    selected.write_bytes(b"# Forged selected document\n")
    snapshot = evidence.snapshot(candidate_source, tmp_path / "forged-candidate")
    control_dir = tmp_path / "control-result"
    control = repair.collect_control(
        Path(writer["spec"].task),
        writer["spec"].task_sha256,
        tmp_path / "forged-candidate",
        snapshot["manifest_sha256"],
        control_dir,
    )
    spec = writer["spec"].model_copy(
        update={
            "result": str(control_dir),
            "result_sha256": control["sha256"],
            "spec_sha256": hashlib.sha256(selected.read_bytes()).hexdigest(),
        }
    )
    with pytest.raises(SkillEvalError, match="retained native writer"):
        handoff.prepare_handoff(spec, tmp_path / "control-handoff")
    record_path = control_dir / "repair-result.json"
    record = json.loads(record_path.read_text())
    genuine = json.loads((writer["result"] / "repair-result.json").read_text())
    record["provenance"] = genuine["provenance"]
    record_path.write_bytes(evidence._json_bytes(record))
    spec = spec.model_copy(update={"result_sha256": file_digest(record_path)})
    # The candidate and patch are internally valid; only native lineage is false.
    repair.result_files(writer["task"], spec.task_sha256, control_dir, spec.result_sha256)
    with pytest.raises(SkillEvalError, match="differs from retained native attempt"):
        handoff.prepare_handoff(spec, tmp_path / "forged-handoff")


def test_readback_rejects_new_input_even_if_its_manifest_is_rebound(writer, tmp_path):
    output = tmp_path / "handoff"
    prepared = handoff.prepare_handoff(writer["spec"], output)
    source = tmp_path / "replacement"
    source.mkdir()
    for name in ("AGENTS.md", "code.py", "scenario.json"):
        (source / name).write_bytes((output / "input" / name).read_bytes())
    (source / "code.py").write_text("unrelated writer implementation must not leak\n")
    doc = source / SPEC_PATH
    doc.parent.mkdir(parents=True)
    doc.write_bytes(SPEC_BYTES)
    captured = evidence.snapshot(source, tmp_path / "replacement-input")
    import shutil

    shutil.rmtree(output / "input")
    shutil.move(tmp_path / "replacement-input", output / "input")
    meta = json.loads((output / "handoff.json").read_text())
    meta["input_sha256"] = captured["manifest_sha256"]
    (output / "handoff.json").write_bytes(evidence._json_bytes(meta))
    with pytest.raises(SkillEvalError, match="original baseline plus selected spec"):
        handoff.load_handoff(output, file_digest(output / "handoff.json"))
    assert prepared["input_digest"] != captured["manifest_sha256"]


def test_existing_output_is_preserved(writer, tmp_path):
    output = tmp_path / "handoff"
    handoff.prepare_handoff(writer["spec"], output)
    before = (output / "handoff.json").read_bytes()
    with pytest.raises(SkillEvalError, match="already exists"):
        handoff.prepare_handoff(writer["spec"], output)
    assert (output / "handoff.json").read_bytes() == before


def test_selected_fresh_vanilla_implementation_is_bound_to_handoff(writer, tmp_path):
    output = tmp_path / "handoff"
    prepared = handoff.prepare_handoff(writer["spec"], output)
    run = tmp_path / "implementation"
    runner.run_native(
        output / "input",
        prepared["input_digest"],
        prepared["prompt"],
        run,
        mode="repair",
        **writer["route"],
    )
    checked = handoff.check_implementation(output, prepared["sha256"], run)
    assert checked["status"] == "completed"
    assert checked["native_receipt_sha256"] == file_digest(run / "native-run.json")
    assert checked["receipt"]["skill_treatment"] == {"arm": "absent", "skill_bundle": None}
    assert checked["semantic_acceptance"] == "unassessed"


@pytest.mark.parametrize("mismatch", ["prompt", "input", "limits"])
def test_implementation_consumer_rejects_different_brief_baseline_or_limits(
    writer, tmp_path, mismatch
):
    output = tmp_path / "handoff"
    prepared = handoff.prepare_handoff(writer["spec"], output)
    input_bundle, input_digest = output / "input", prepared["input_digest"]
    prompt = prepared["prompt"]
    kwargs = {}
    if mismatch == "prompt":
        prompt = "Another implementation request."
    elif mismatch == "input":
        input_bundle = Path(writer["task"].input_bundle)
        input_digest = writer["task"].input_sha256
    else:
        kwargs["native_timeout_seconds"] = 60
    run = tmp_path / "implementation"
    runner.run_native(
        input_bundle, input_digest, prompt, run, mode="repair", **writer["route"], **kwargs
    )
    with pytest.raises(SkillEvalError, match=r"handoff input or prompt|limits differ"):
        handoff.check_implementation(output, prepared["sha256"], run)


def test_implementation_with_selected_skill_is_ineligible(writer, tmp_path):
    from test_treatments import captured

    output = tmp_path / "handoff"
    prepared = handoff.prepare_handoff(writer["spec"], output)
    skill, skill_digest = captured(tmp_path)
    run = tmp_path / "implementation"
    runner.run_native(
        output / "input",
        prepared["input_digest"],
        prepared["prompt"],
        run,
        mode="repair",
        skill_bundle=skill,
        skill_digest=skill_digest,
        **writer["route"],
    )
    with pytest.raises(SkillEvalError, match="absent selected skill"):
        handoff.check_implementation(output, prepared["sha256"], run)


def test_audit_cannot_hide_skill_installation_behind_compact_absent_receipt(writer, tmp_path):
    output = tmp_path / "handoff"
    prepared = handoff.prepare_handoff(writer["spec"], output)
    run = tmp_path / "implementation"
    runner.run_native(
        output / "input",
        prepared["input_digest"],
        prepared["prompt"],
        run,
        mode="repair",
        **writer["route"],
    )
    # A valid known-positive implementation precedes the contradictory audit.
    handoff.check_implementation(output, prepared["sha256"], run)
    audit_path = run / "native-audit.json"
    audit = json.loads(audit_path.read_text())
    audit["skill_treatment"] = {"arm": "available", "skill_name": "hidden-selection"}
    audit_path.write_bytes(evidence._json_bytes(audit))
    receipt_path = run / "native-run.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["audit"].update(bytes=audit_path.stat().st_size, sha256=file_digest(audit_path))
    receipt_path.write_bytes(evidence._json_bytes(receipt))
    with pytest.raises(SkillEvalError, match="absent selected skill"):
        handoff.check_implementation(output, prepared["sha256"], run)


@pytest.mark.parametrize("suffix", [".markdown", ".MD", ".MARKDOWN"])
def test_markdown_suffix_is_a_supported_selected_document(writer_factory, tmp_path, suffix):
    name = "docs/specs/feature" + suffix
    attempt = writer_factory(files={name: SPEC_BYTES.decode()})
    spec = attempt["spec"].model_copy(update={"spec_path": name})
    # Validate through the public input model, not model_copy's unchecked update.
    spec = handoff.SpecHandoffSpec.model_validate_json(spec.model_dump_json())
    prepared = handoff.prepare_handoff(spec, tmp_path / "handoff")
    handoff.load_handoff(tmp_path / "handoff", prepared["sha256"])
    assert (tmp_path / "handoff/input" / name).read_bytes() == SPEC_BYTES


def test_cli_schema_prepare_verify_and_check_consumer(writer, tmp_path, capsys):
    from skilleval import cli

    assert cli.main(["schema", "spec-handoff-spec"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert "spec_sha256" in schema["properties"]
    selected = tmp_path / "handoff-spec.json"
    selected.write_text(writer["spec"].model_dump_json())
    output = tmp_path / "handoff"
    assert cli.main(["handoff", "prepare", str(selected), "--output", str(output)]) == 0
    prepared = json.loads(capsys.readouterr().out)
    assert cli.main(["handoff", "verify", str(output), "--sha256", prepared["sha256"]]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "verified"
    run = tmp_path / "implementation"
    runner.run_native(
        output / "input",
        prepared["input_digest"],
        prepared["prompt"],
        run,
        mode="repair",
        **writer["route"],
    )
    assert (
        cli.main(
            [
                "handoff",
                "check-implementation",
                str(output),
                "--sha256",
                prepared["sha256"],
                "--run",
                str(run),
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["status"] == "completed"
    assert cli.main(["handoff", "verify", str(output), "--sha256", "0" * 64]) == 2
    result = capsys.readouterr()
    assert result.out == ""
    assert json.loads(result.err)["status"] == "error"
