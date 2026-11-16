"""Host-native observation and retained-artifact boundaries, without inference."""

import io
import sys
import tarfile
from pathlib import Path

import pytest

from skilleval import native_process, runner
from skilleval.claude_runner import _merge_artifacts
from skilleval.errors import SkillEvalError


def archive(path: Path, entries: dict[str, bytes]) -> None:
    with tarfile.open(path, "w") as target:
        for name, raw in entries.items():
            item = tarfile.TarInfo(name)
            item.size = len(raw)
            target.addfile(item, io.BytesIO(raw))


def test_host_evidence_cannot_be_forged_by_guest_archive(tmp_path: Path) -> None:
    guest = tmp_path / "guest.tar"
    archive(guest, {"evidence/final.txt": b"guest counterfeit", "task/input": b"source"})
    with pytest.raises(SkillEvalError, match="collide"):
        _merge_artifacts(
            guest, tmp_path / "combined.tar", {"evidence/final.txt": b"native"}, 1048576, 100
        )
    assert not (tmp_path / "combined.tar").exists()


def test_guest_source_and_host_native_output_have_separate_exact_members(tmp_path: Path) -> None:
    guest = tmp_path / "guest.tar"
    archive(guest, {"task/input": b"source", "evidence/commands/1.json": b"{}"})
    original = guest.read_bytes()
    result = _merge_artifacts(
        guest, tmp_path / "combined.tar", {"evidence/final.txt": b"native"}, 1048576, 100
    )
    assert {row["path"] for row in result["entries"]} == {
        "task/input",
        "evidence/commands/1.json",
        "evidence/final.txt",
    }
    assert guest.read_bytes() == original
    with tarfile.open(tmp_path / "combined.tar") as source:
        member = source.extractfile("evidence/final.txt")
        assert member is not None and member.read() == b"native"


@pytest.mark.parametrize("reject", [True, False])
def test_native_init_observation_precedes_child_effect(tmp_path: Path, reject: bool) -> None:
    prompt = tmp_path / "prompt"
    prompt.write_text("ordinary request")
    marker = tmp_path / "effect"
    code = (
        "import sys,json,time,pathlib; sys.stdin.read(); "
        "print(json.dumps({'type':'system','subtype':'init'}),flush=True); "
        "time.sleep(0.8); pathlib.Path(sys.argv[1]).write_text('effect')"
    )
    seen = []

    def observer(record: dict) -> None:
        seen.append(record)
        if reject:
            raise ValueError("unexpected native tools")

    result = native_process._execute_native(
        [sys.executable, "-I", "-c", code, str(marker)],
        {},
        prompt,
        tmp_path,
        tmp_path,
        5,
        event_observer=observer,
    )
    assert seen == [{"type": "system", "subtype": "init"}]
    assert marker.exists() is not reject
    if reject:
        assert result["protocol_error"] == "unexpected native tools"
        assert result["timed_out"] is False
    else:
        assert result["returncode"] == 0
        assert result["protocol_error"] is None


def test_claude_dispatch_omits_codex_credentials_and_catalog(tmp_path: Path, monkeypatch) -> None:
    import skilleval.claude_runner as adapter

    observed = []
    monkeypatch.setattr(
        adapter,
        "run_claude_judge",
        lambda *args, **kwargs: observed.append(kwargs) or {"status": "probe"},
    )
    actual = runner.run_native(
        tmp_path / "input",
        "a" * 64,
        "request",
        tmp_path / "output",
        runtime=tmp_path / "runtime",
        image=tmp_path / "image",
        image_sha256="b" * 64,
        mode="judge",
        investigation=False,
        judge_profile="claude-opus",
    )
    assert actual == {"status": "probe"}
    assert "auth_file" not in observed[0] and "catalog" not in observed[0]
    for options in (
        {"auth_file": tmp_path / "auth"},
        {"catalog": tmp_path / "catalog"},
        {"mode": "repair"},
    ):
        invocation = {
            "mode": "judge",
            "investigation": False,
            "judge_profile": "claude-opus",
            **options,
        }
        with pytest.raises(SkillEvalError, match="Claude supports judge"):
            runner.run_native(
                tmp_path / "input",
                "a" * 64,
                "request",
                tmp_path / "output",
                runtime=tmp_path / "runtime",
                image=tmp_path / "image",
                image_sha256="b" * 64,
                **invocation,
            )
    assert len(observed) == 1
    assert not (tmp_path / "output").exists()


def test_claude_cli_selection_has_no_required_codex_auth() -> None:
    from skilleval.cli import parser

    parsed = parser().parse_args(
        [
            "native",
            "qualify",
            "input",
            "--sha256",
            "a" * 64,
            "--prompt",
            "prompt",
            "--mode",
            "judge",
            "--judge-policy",
            "static",
            "--judge",
            "claude-opus",
            "--runtime",
            "runtime",
            "--image-archive",
            "image",
            "--image-sha256",
            "b" * 64,
            "--output",
            "new-run",
        ]
    )
    assert parsed.judge == "claude-opus"
    assert parsed.catalog is None and parsed.auth_file is None
    correction = parser().parse_args(
        [
            "repair",
            "prepare-citation-correction",
            "spec.json",
            "--packet",
            "packet",
            "--run",
            "run",
            "--output",
            "output",
        ]
    )
    assert correction.max_requests is None
