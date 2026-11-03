"""Authored archives test bounded parsing; no VM or imported task code executes."""

import hashlib
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_runner import route as route

from skilleval import evidence, guest_runner, judge, repair, runner
from skilleval.errors import SkillEvalError
from skilleval.qualification import _plain_tar_headers


def record(key: bytes, value: bytes) -> bytes:
    body = key + b"=" + value + b"\n"
    size = len(body) + 2
    while (actual := len(body) + len(str(size)) + 1) != size:
        size = actual
    return str(size).encode() + b" " + body


def member(name: str, payload: bytes = b"", kind: bytes = tarfile.REGTYPE) -> bytes:
    info = tarfile.TarInfo(name)
    info.type, info.size = kind, len(payload)
    return info.tobuf(format=tarfile.USTAR_FORMAT) + payload + b"\0" * (-len(payload) % 512)


def extension(payload: bytes, kind: bytes = tarfile.XHDTYPE) -> bytes:
    return member("local-pax", payload, kind)


def write_archive(tmp_path: Path, *parts: bytes, end: bool = True) -> Path:
    path = tmp_path / "artifacts.tar"
    path.write_bytes(b"".join(parts) + (b"\0" * 1024 if end else b""))
    return path


def layout(tmp_path: Path) -> tuple[Path, Path]:
    root, work = tmp_path / "native", tmp_path / "task"
    for directory in (root / "evidence", root / "home", root / "codex-home", work):
        directory.mkdir(parents=True)
    return root, work


def test_actual_collector_preserves_long_task_and_npm_cache_leaf_names(tmp_path):
    root, work = layout(tmp_path)
    task = work / ("independently-chosen-" + "a" * 110 + ".ts")
    task.write_bytes(b"authored task bytes\n")
    marker = root / "home/.npm/_update-notifier-last-checked"
    marker.parent.mkdir()
    marker.write_bytes(b"")
    cache = root / "home/.npm/_cacache/content-v2/sha512/aa/bb" / ("c" * 124)
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"authored npm cache bytes\n")
    result = guest_runner._archive(root, work, 1024 * 1024, 100)
    assert result["status"] == "complete"
    archive = root / "artifacts.tar"
    observed = runner.validate_artifacts(archive, 1024 * 1024, 100)
    names = {row["path"]: row for row in observed["entries"]}
    for name, data in (
        ("task/" + task.name, task.read_bytes()),
        ("home/" + cache.relative_to(root / "home").as_posix(), cache.read_bytes()),
    ):
        assert names[name]["sha256"] == hashlib.sha256(data).hexdigest()
        assert runner._member(archive, name, 1024) == data
    with tarfile.open(archive) as source:
        assert [set(item.pax_headers) for item in source if item.pax_headers] == [
            {"path"},
            {"path"},
        ]
    with archive.open("rb") as raw, pytest.raises(SkillEvalError, match=r"header|extension"):
        _plain_tar_headers(raw)


def test_short_names_preserve_legacy_archive_bytes_and_budget(tmp_path, monkeypatch):
    root, work = layout(tmp_path)
    (work / "source.ts").write_bytes(b"ordinary source\n")
    assert guest_runner._archive(root, work, 20_000, 100)["status"] == "complete"
    actual = (root / "artifacts.tar").read_bytes()
    original = guest_runner.tarfile
    monkeypatch.setattr(
        guest_runner,
        "tarfile",
        SimpleNamespace(
            open=original.open, TarInfo=original.TarInfo, PAX_FORMAT=tarfile.USTAR_FORMAT
        ),
    )
    assert guest_runner._archive(root, work, 20_000, 100)["status"] == "complete"
    assert (root / "artifacts.tar").read_bytes() == actual


@pytest.mark.parametrize("key", [b"size", b"linkpath", b"GNU.sparse.map", b"mtime", b"hdrcharset"])
def test_unknown_pax_fields_rejected_before_member_reads(tmp_path, key):
    archive = write_archive(
        tmp_path, extension(record(key, b"task/file")), member("evidence/final.txt", b"safe")
    )
    with pytest.raises(SkillEvalError, match="PAX"):
        runner.validate_artifacts(archive, 1024 * 1024, 100)
    with pytest.raises(SkillEvalError, match="PAX"):
        runner._member(archive, "evidence/final.txt", 1024)


@pytest.mark.parametrize(
    "payload",
    [
        b"0 path=task/file\n",
        b"017 path=task/file\n",
        b"99 path=task/file\n",
        b"17 path=task/file",
        record(b"path", b""),
        record(b"path", b"task/bad\nrecord"),
        record(b"path", b"task/bad\0record"),
        record(b"path", b"task/\xff"),
        record(b"path", b"task/one") + record(b"path", b"task/two"),
    ],
)
def test_malformed_or_duplicate_pax_records_rejected(tmp_path, payload):
    archive = write_archive(tmp_path, extension(payload), member("task/file", b"safe"))
    with pytest.raises(SkillEvalError, match="PAX"):
        runner.validate_artifacts(archive, 1024 * 1024, 100)


@pytest.mark.parametrize(
    "path", [b"/escape", b"task/../escape", b"task//alias", b"task/back\\slash"]
)
def test_pax_names_still_require_canonical_safe_paths(tmp_path, path):
    archive = write_archive(tmp_path, extension(record(b"path", path)), member("placeholder", b"x"))
    with pytest.raises(SkillEvalError, match="unsafe"):
        runner.validate_artifacts(archive, 1024 * 1024, 100)
    assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize(
    "kind", [tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK]
)
def test_global_and_gnu_extensions_remain_forbidden(tmp_path, kind):
    archive = write_archive(
        tmp_path, extension(record(b"path", b"task/file"), kind), member("task/file", b"safe")
    )
    with pytest.raises(SkillEvalError, match=r"header|extension"):
        runner.validate_artifacts(archive, 1024 * 1024, 100)


@pytest.mark.parametrize(
    "following", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE, tarfile.GNUTYPE_SPARSE]
)
def test_pax_path_must_precede_one_regular_member(tmp_path, following):
    archive = write_archive(
        tmp_path, extension(record(b"path", b"task/file")), member("placeholder", kind=following)
    )
    with pytest.raises(SkillEvalError, match=r"header|extension"):
        runner.validate_artifacts(archive, 1024 * 1024, 100)


def test_chained_dangling_oversized_and_truncated_extensions_rejected(tmp_path):
    pax = extension(record(b"path", b"task/file"))
    cases = [
        (pax + pax + member("task/file"), True),
        (pax, True),
        (extension(record(b"path", b"task/" + b"x" * 4096)) + member("task/file"), True),
        (pax + member("task/file"), False),
        (pax[:600], False),
    ]
    for content, end in cases:
        archive = write_archive(tmp_path, content, end=end)
        with pytest.raises(SkillEvalError):
            runner.validate_artifacts(archive, 1024 * 1024, 100)


def test_native_logical_and_physical_header_counts_are_bounded(tmp_path):
    archive = write_archive(tmp_path, member("task/one"), member("task/two"))
    with pytest.raises(SkillEvalError, match=r"inventory|header"):
        runner.validate_artifacts(archive, 1024 * 1024, 1)
    archive = write_archive(
        tmp_path,
        extension(record(b"path", b"task/one")),
        member("placeholder"),
        extension(record(b"path", b"task/two")),
        member("placeholder"),
    )
    with pytest.raises(SkillEvalError, match=r"inventory|header"):
        runner.validate_artifacts(archive, 1024 * 1024, 1)


def test_pax_resolved_names_still_reject_case_aliases(tmp_path):
    archive = write_archive(
        tmp_path,
        extension(record(b"path", b"task/file")),
        member("placeholder"),
        extension(record(b"path", b"task/FILE")),
        member("placeholder"),
    )
    with pytest.raises(SkillEvalError, match="unsafe"):
        runner.validate_artifacts(archive, 1024 * 1024, 100)


def test_guest_and_reader_pax_budgets_agree():
    assert guest_runner.NATIVE_PAX_PATH_BYTES == runner.NATIVE_PAX_PATH_BYTES == 4096


def test_collection_failure_retains_bounded_primary_error(tmp_path, monkeypatch):
    root, work = tmp_path / "native", tmp_path / "task"
    root.mkdir()
    work.mkdir()
    (root / "config.toml").write_text("")
    (root / "settings.json").write_text(
        json.dumps(
            {
                "expected_codex_home": [],
                "max_artifact_bytes": 1024 * 1024,
                "max_artifact_files": 100,
            }
        )
    )
    monkeypatch.setattr(guest_runner.os, "getuid", lambda: 0)

    def refuse_preflight(*args):
        raise ValueError("authored preflight refusal")

    def refuse_archive(*args):
        raise OSError("authored collector failure " + "x" * 3000)

    monkeypatch.setattr(guest_runner.context_guard, "preflight", refuse_preflight)
    monkeypatch.setattr(guest_runner, "_archive", refuse_archive)
    assert guest_runner.run(root, work) == 24
    observed = json.loads((root / "collection.json").read_text())
    assert observed["status"] == "failed"
    assert observed["error_type"] == "OSError"
    assert observed["error"].startswith("authored collector failure")
    assert len(observed["error"]) == 1024


def test_native_run_readback_accepts_long_cache_and_task_paths(tmp_path):
    from test_judge import run_directory

    source = tmp_path / "source"
    source.mkdir()
    (source / "code.py").write_text("raise RuntimeError('must never execute')\n")
    captured = evidence.snapshot(source, tmp_path / "input")
    long_task = "task/src/" + "a" * 124 + ".py"
    long_cache = "home/.npm/_cacache/content-v2/sha512/aa/bb/" + "c" * 124
    run = run_directory(
        tmp_path,
        {
            "path": str(source),
            "input_digest": captured["manifest_sha256"],
            "prompt": "Repair the authored fixture.",
            "investigation": False,
        },
        {},
        mode="repair",
        artifacts={
            long_task: b"authored source",
            long_cache: b"retained cache",
            "evidence/filesystem-links.json": b"[]",
        },
    )
    _, _, artifacts = judge._run_files(run, mode="repair")
    assert artifacts[long_task] == b"authored source"
    with tarfile.open(run / "artifacts.tar") as archive:
        assert archive.extractfile(long_cache).read() == b"retained cache"


def test_repair_collection_accepts_new_independently_named_long_source(tmp_path, route):
    from skilleval.identity import file_digest

    source = tmp_path / "source"
    source.mkdir()
    name = "src/" + "independent-" + "a" * 110 + ".py"
    (source / "code.py").write_text("raise RuntimeError('must never execute')\n")
    (source / "scenario.json").write_text(
        json.dumps(
            {
                "repair_files": {name: "authored repair source\n"},
            }
        )
    )
    frozen = tmp_path / "input"
    captured = evidence.snapshot(source, frozen)
    worker = tmp_path / "worker.py"
    worker.write_text("raise RuntimeError('VM-only authored observer')\n")
    spec = repair.RepairSpec(
        task_id="authored-long-path",
        input_bundle=str(frozen),
        input_sha256=captured["manifest_sha256"],
        prompt="Repair the authored fixture.",
        editable_paths=("code.py",),
        editable_directories=("src",),
        worker=str(worker),
        worker_sha256=file_digest(worker),
        probes=(repair.RepairProbe(probe_id="authored", request={"value": True}, expected=True),),
        source_url="https://example.invalid/authored",
        source_revision="v1",
    )
    contract = tmp_path / "task.json"
    prepared = repair.prepare_repair(spec, contract)
    run = tmp_path / "native"
    assert (
        runner.run_native(frozen, spec.input_sha256, spec.prompt, run, mode="repair", **route)[
            "status"
        ]
        == "completed"
    )
    output = tmp_path / "collected"
    result = repair.collect_repair(contract, prepared["sha256"], run, output)
    assert result["status"] == "collected"
    assert result["changed_paths"] == [name]
    assert (output / "candidate" / name).read_bytes() == b"authored repair source\n"


def test_collected_long_source_can_reach_offline_checker(tmp_path, route):
    from skilleval.identity import file_digest
    from skilleval.repair_check import check_repair

    source = tmp_path / "source"
    source.mkdir()
    name = "src/" + "a" * 124 + ".py"
    (source / "code.py").write_text("raise RuntimeError('must never execute')\n")
    (source / "scenario.json").write_text(json.dumps({"repair_files": {name: "authored source"}}))
    frozen = tmp_path / "input"
    captured = evidence.snapshot(source, frozen)
    worker = tmp_path / "worker.py"
    worker.write_text("raise RuntimeError('VM-only authored observer')\n")
    spec = repair.RepairSpec(
        task_id="authored-long-checker",
        input_bundle=str(frozen),
        input_sha256=captured["manifest_sha256"],
        prompt="Repair the authored fixture.",
        editable_paths=("code.py",),
        editable_directories=("src",),
        worker=str(worker),
        worker_sha256=file_digest(worker),
        probes=(repair.RepairProbe(probe_id="authored", request={"value": True}, expected=True),),
        source_url="https://example.invalid/authored",
        source_revision="v1",
    )
    contract = tmp_path / "task.json"
    prepared = repair.prepare_repair(spec, contract)
    run = tmp_path / "native"
    assert (
        runner.run_native(frozen, spec.input_sha256, spec.prompt, run, mode="repair", **route)[
            "status"
        ]
        == "completed"
    )
    output = tmp_path / "collected"
    result = repair.collect_repair(contract, prepared["sha256"], run, output)
    report = check_repair(
        contract,
        prepared["sha256"],
        output,
        result["sha256"],
        tmp_path / "checked",
        runtime=route["runtime"],
        image=route["image"],
        image_sha256=route["image_sha256"],
    )
    assert report["status"] == "completed"
    assert report["accepted_by_checks"] is True


def test_host_task_transfer_supports_long_names_and_preserves_short_archive_bytes(tmp_path):
    import io

    ordinary = [("task/source.ts", b"ordinary bytes", False)]
    short_archive = tmp_path / "short.tar"
    runner._tar(short_archive, ordinary)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        info = tarfile.TarInfo("task/source.ts")
        info.size, info.mode = len(b"ordinary bytes"), 0o644
        archive.addfile(info, io.BytesIO(b"ordinary bytes"))
    assert short_archive.read_bytes() == buffer.getvalue()
    long_name = "task/src/" + "a" * 124 + ".py"
    long_archive = tmp_path / "long.tar"
    runner._tar(long_archive, [(long_name, b"authored source", True)])
    assert (
        runner.validate_artifacts(long_archive, 1024 * 1024, 100)["entries"][0]["path"] == long_name
    )
    assert runner._member(long_archive, long_name, 1024) == b"authored source"


def test_manifest_budget_stays_bounded_when_paths_expand(tmp_path, monkeypatch):
    archive = write_archive(tmp_path, member("task/file", b"authored bytes"))
    assert runner.validate_artifacts(archive, 1024 * 1024, 100)["entries"]
    monkeypatch.setattr(evidence, "MAX_MANIFEST_BYTES", 128)
    with pytest.raises(SkillEvalError, match="manifest"):
        runner.validate_artifacts(archive, 1024 * 1024, 100)


def test_host_task_transfer_rejects_oversized_pax_before_writing(tmp_path):
    archive = tmp_path / "oversized.tar"
    with pytest.raises(SkillEvalError, match="PAX metadata"):
        runner._tar(archive, [("task/" + "a" * 4096, b"authored", False)])
    assert not archive.exists()
