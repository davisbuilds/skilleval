"""Control CLI preflight only; real lane state/reuse proof requires an opt-in VM run."""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/qualify-lanes.py"


def invoke(*args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        text=True,
        capture_output=True,
        check=False,
    )


def arguments(tmp_path: Path) -> dict[str, object]:
    return {
        "--runtime": tmp_path / "unavailable-runtime",
        "--image-archive": tmp_path / "unavailable-image.tar",
        "--image-sha256": "0" * 64,
        "--output": tmp_path / "evidence",
    }


def flatten(options: dict[str, object]) -> list[object]:
    return [item for pair in options.items() for item in pair]


def test_help_does_not_require_a_runtime_or_create_evidence(tmp_path: Path) -> None:
    result = invoke("--help", "--output", tmp_path / "evidence")
    assert result.returncode == 0, result.stderr
    assert "--runtime" in result.stdout
    assert "--recover" in result.stdout
    assert not (tmp_path / "evidence").exists()


@pytest.mark.parametrize("missing", ["--runtime", "--image-archive", "--image-sha256", "--output"])
def test_run_requires_all_pins_and_output_before_creating_evidence(
    tmp_path: Path, missing: str
) -> None:
    options = arguments(tmp_path)
    del options[missing]
    result = invoke(*flatten(options))
    assert result.returncode == 2
    assert "provide --runtime, --image-archive, --image-sha256 and --output" in result.stderr
    assert not (tmp_path / "evidence").exists()


def test_existing_evidence_is_never_replaced(tmp_path: Path) -> None:
    output = tmp_path / "evidence"
    output.mkdir()
    receipt = output / "qualification.json"
    receipt.write_bytes(b"retained original receipt\n")
    result = invoke(*flatten(arguments(tmp_path)))
    assert result.returncode == 2
    assert "--output must be a fresh directory" in result.stderr
    assert receipt.read_bytes() == b"retained original receipt\n"
    assert list(output.iterdir()) == [receipt]


@pytest.mark.parametrize("digest", ["", "g" * 64, "A" * 64, "0" * 63])
def test_invalid_image_pin_is_refused_before_creating_output(tmp_path: Path, digest: str) -> None:
    options = arguments(tmp_path)
    options["--image-sha256"] = digest
    result = invoke(*flatten(options))
    assert result.returncode == 2
    assert not (tmp_path / "evidence").exists()


def test_recovery_cannot_silently_ignore_run_arguments(tmp_path: Path) -> None:
    result = invoke("--recover", tmp_path / "ticket", *flatten(arguments(tmp_path)))
    assert result.returncode == 2
    assert "--recover cannot be combined with run arguments" in result.stderr
    assert not (tmp_path / "evidence").exists()
