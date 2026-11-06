"""Exercise the operator CLI with real immutable files and no imported execution."""

import json

from test_acceptance import mapped as mapped
from test_acceptance import route as route
from test_acceptance import task as task
from test_cli import invoke


def test_acceptance_cli_prepares_inspects_and_renders(mapped, tmp_path):
    source = tmp_path / "spec.json"
    source.write_text(json.dumps(mapped))
    target = tmp_path / "map.json"
    prepared = invoke("task-package", "prepare-acceptance", str(source), "--output", str(target))
    assert prepared.returncode == 0, prepared.stderr
    receipt = json.loads(prepared.stdout)
    rendered = tmp_path / "review.md"
    inspected = invoke(
        "task-package",
        "inspect-acceptance",
        str(target),
        "--sha256",
        receipt["sha256"],
        "--review-output",
        str(rendered),
    )
    assert inspected.returncode == 0, inspected.stderr
    summary = json.loads(inspected.stdout)
    assert summary["semantic_validity"] == "unassessed"
    assert summary["scored_admitted"] is False
    assert "R1" in rendered.read_text() and "Unfixed result is false" in rendered.read_text()
    assert (
        invoke(
            "task-package", "prepare-acceptance", str(source), "--output", str(target)
        ).returncode
        == 2
    )
    target.write_text("{}")
    rejected = invoke(
        "task-package", "inspect-acceptance", str(target), "--sha256", receipt["sha256"]
    )
    assert rejected.returncode == 2 and "digest" in rejected.stderr


def test_acceptance_schema_and_structural_validation_are_offline(mapped, tmp_path):
    schema = invoke("schema", "acceptance-map-spec")
    assert schema.returncode == 0
    assert "requirements" in json.loads(schema.stdout)["properties"]
    source = tmp_path / "missing-inputs.json"
    mapped["task"] = "/missing/task.json"
    source.write_text(json.dumps(mapped))
    # Generic validation intentionally validates declaration shape only.
    assert invoke("validate", "acceptance-map-spec", str(source)).returncode == 0
    prepared = invoke(
        "task-package", "prepare-acceptance", str(source), "--output", str(tmp_path / "map.json")
    )
    assert prepared.returncode == 2
