"""Source capture's public CLI uses pinned Git data without running source code."""

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from test_cli import invoke


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "-c", "core.hooksPath=/dev/null", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def source(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Source capture fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    (root / "AGENTS.md").write_text("Keep ordinary instructions.\n")
    (root / "module.py").write_text("raise RuntimeError('never execute captured source')\n")
    (root / "test_module.py").write_text("def test_example():\n    assert True\n")
    (root / "specs").mkdir()
    (root / "specs" / "plan.md").write_text("Historical plan context.\n")
    git(root, "add", "AGENTS.md", "module.py", "test_module.py", "specs/plan.md")
    git(root, "commit", "-qm", "Retained source fixture")
    return root, git(root, "rev-parse", "HEAD")


@pytest.mark.parametrize("exclude_specs", [False, True])
def test_source_capture_cli_freezes_revision_and_optional_policy(
    source: tuple[Path, str], tmp_path: Path, exclude_specs: bool
) -> None:
    root, revision = source
    # The command reads the named revision, not uncommitted working-tree bytes.
    (root / "module.py").write_text("changed after the selected revision\n")
    output = tmp_path / "capture"
    args = ["source", "capture", str(root), "--revision", revision, "--output", str(output)]
    if exclude_specs:
        policy = tmp_path / "exclusions.json"
        policy.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "exclude": [{"path": "specs", "reason": "Declared plan-context intervention"}],
                }
            )
        )
        args.extend(["--exclusions", str(policy)])
    captured = invoke(*args)
    assert captured.returncode == 0, captured.stderr
    assert not captured.stderr
    receipt = json.loads(captured.stdout)
    baseline = Path(receipt["baseline"]["path"])
    assert baseline == output / "baseline"
    assert receipt["source_revision"] == revision
    assert receipt["excluded_file_count"] == int(exclude_specs)
    assert (baseline / "AGENTS.md").read_text() == "Keep ordinary instructions.\n"
    assert (baseline / "module.py").read_text().startswith("raise RuntimeError")
    assert (baseline / "test_module.py").is_file()
    assert (baseline / "specs" / "plan.md").exists() is not exclude_specs
    assert not (baseline / "exclusions.json").exists()
    assert not (baseline / "provenance.json").exists()
    assert (
        hashlib.sha256((output / "provenance.json").read_bytes()).hexdigest()
        == receipt["provenance_sha256"]
    )
    verified = invoke("verify", str(baseline), "--sha256", receipt["baseline"]["manifest_sha256"])
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout)["kind"] == "snapshot"
    # Fresh destinations protect earlier generations from replacement.
    rejected = invoke(*args)
    assert rejected.returncode == 2
    assert not rejected.stdout


def test_source_exclusions_schema_and_validation_are_structural(tmp_path: Path) -> None:
    schema = invoke("schema", "source-exclusions")
    assert schema.returncode == 0, schema.stderr
    document = json.loads(schema.stdout)
    assert document["properties"]["schema_version"]["const"] == 1
    assert "exclude" in document["properties"]
    policy = tmp_path / "policy.json"
    value = {
        "schema_version": 1,
        "exclude": [{"path": "not-yet-resolved/specs", "reason": "Explicit context selection"}],
    }
    policy.write_text(json.dumps(value))
    validated = invoke("validate", "source-exclusions", str(policy))
    assert validated.returncode == 0, validated.stderr
    assert json.loads(validated.stdout) == {"status": "valid-structure", "record": value}


@pytest.mark.parametrize(
    "contents",
    [
        '{"schema_version":1,"schema_version":1,"exclude":[]}',
        '{"schema_version":1,"exclude":[{"path":"specs","reason":""}]}',
        '{"schema_version":1,"exclude":[{"path":"specs/**","reason":"private context"}]}',
    ],
)
def test_source_capture_rejects_invalid_policy_before_publishing(
    source: tuple[Path, str], tmp_path: Path, contents: str
) -> None:
    root, revision = source
    policy = tmp_path / "policy.json"
    policy.write_text(contents)
    output = tmp_path / "capture"
    result = invoke(
        "source",
        "capture",
        str(root),
        "--revision",
        revision,
        "--exclusions",
        str(policy),
        "--output",
        str(output),
    )
    assert result.returncode == 2
    assert not result.stdout
    assert json.loads(result.stderr)["status"] == "error"
    assert not output.exists()


def test_source_capture_rejects_unmatched_policy_at_selected_revision(
    source: tuple[Path, str], tmp_path: Path
) -> None:
    root, revision = source
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps(
            {"schema_version": 1, "exclude": [{"path": "missing", "reason": "Context selection"}]}
        )
    )
    output = tmp_path / "capture"
    result = invoke(
        "source",
        "capture",
        str(root),
        "--revision",
        revision,
        "--exclusions",
        str(policy),
        "--output",
        str(output),
    )
    assert result.returncode == 2
    assert not result.stdout
    assert json.loads(result.stderr)["status"] == "error"
    assert not output.exists()
