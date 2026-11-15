"""Authored filesystem/tools only; no imported candidate runs on the host."""

import hashlib
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "examples/document/operational-metrics"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


observer = load("projection_observer", ROOT / "downstream/validate.py")
environment = load("projection_environment", ROOT / "environment/validate.py")


def test_generated_dependencies_are_retained_but_scratch_uses_qualified_image(
    tmp_path, monkeypatch
):
    source = tmp_path / "candidate"
    (source / "frontend/src/empty-directory").mkdir(parents=True)
    (source / "frontend/src/view.svelte").write_text("authored view\n")
    (source / "package.json").write_text('{"name":"authored-original"}\n')
    baseline = tmp_path / "baseline"
    shutil.copytree(source, baseline)
    changed_manifest = '{"name":"authored-dependency-edit","dependencies":{"new":"1"}}\n'
    (source / "package.json").write_text(changed_manifest)
    (source / "frontend/src/node_modules-not-cache").write_text("ordinary source stays\n")
    caches = {
        "node_modules/.cache/generated.bin": b"root generated cache\x00",
        "frontend/node_modules/.vite/build-cache.json": b'{"generated":"frontend"}',
        "docs/node_modules/nested/cache.txt": b"nested generated cache",
    }
    for name, data in caches.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (source / "node_modules/.cache/empty").mkdir()
    outside = tmp_path / "outside-cache-target"
    outside.write_text("must not be inventoried through the link")
    (source / "node_modules/cache-link").symlink_to(outside)
    dependencies = tmp_path / "qualified-image-dependencies"
    (dependencies / ".bin").mkdir(parents=True)
    (dependencies / "pinned-package").mkdir()
    (dependencies / "pinned-package/identity.txt").write_text("qualified image bytes")
    tools = tmp_path / "authored-tools"
    tools.mkdir()
    build = tools / "pnpm"
    build.write_text(
        f"#!{sys.executable}\n"
        + """
from pathlib import Path
project = Path.cwd()
assert (project/'frontend/src/view.svelte').read_text() == 'authored view\\n'
assert (project/'frontend/src/empty-directory').is_dir()
assert (project/'frontend/src/node_modules-not-cache').read_text() == 'ordinary source stays\\n'
assert 'authored-dependency-edit' in (project/'package.json').read_text()
assert not (project/'docs/node_modules').exists()
for relative in ('node_modules', 'frontend/node_modules'):
    modules = project/relative
    assert not (modules/'.cache').exists()
    assert not (modules/'.vite').exists()
    assert not (modules/'cache-link').exists()
    assert (modules/'pinned-package').is_symlink()
    assert (modules/'pinned-package/identity.txt').read_text() == 'qualified image bytes'
print('qualified scratch dependency projection reached build')
"""
    )
    browser = tools / "node"
    browser.write_text(
        f"#!{sys.executable}\n"
        + """
import json, sys
from pathlib import Path
output = Path(sys.argv[5])
output.mkdir()
(output/'observation.json').write_text(json.dumps({'execution_complete': True}))
"""
    )
    for tool in (build, browser):
        tool.chmod(0o755)
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ["PATH"])
    output = tmp_path / "observations"
    result = observer.run(source, baseline, dependencies, output, {}, environment)

    # The old copytree reaches bridge_dependencies and fails on the retained cache.
    assert result["execution_status"] == "observed", result
    assert result["candidate_source_changes"] == []
    assert result["build_changed_candidate_inputs"] == []
    for name, data in caches.items():
        assert (source / name).read_bytes() == data
    assert (source / "node_modules/.cache/empty").is_dir()
    assert os.readlink(source / "node_modules/cache-link") == str(outside)
    assert (source / "package.json").read_text() == changed_manifest
    assert not (source / "node_modules/pinned-package").exists()
    assert (
        "qualified scratch dependency projection reached build"
        in (output / "command-0/stdout").read_text()
    )

    receipt_bytes = (output / "scratch-projection.json").read_bytes()
    assert result["scratch_projection"] == {
        "path": "scratch-projection.json",
        "sha256": hashlib.sha256(receipt_bytes).hexdigest(),
    }
    receipt = json.loads(receipt_bytes)
    assert receipt["omitted_dependency_roots"] == [
        "docs/node_modules",
        "frontend/node_modules",
        "node_modules",
    ]
    inventory = receipt["omitted_dependency_inventory"]
    for name, data in caches.items():
        assert inventory[name] == {
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "executable": False,
        }
    assert inventory["node_modules/.cache/empty"] == {"directory": True}
    assert inventory["node_modules/cache-link"] == {"symlink": str(outside)}
    assert not any("outside-cache-target" in name for name in inventory)
    assert (
        receipt["omitted_dependency_inventory_sha256"]
        == hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()
    )
    assert receipt["dependency_edit_authorization"] == "none; source review required"
    assert receipt["qualified_dependency_directory"] == str(dependencies)
    assert "scratch" in receipt["rationale"]
    assert "changes.patch" in receipt["candidate_review_authority"]
    source_observation = json.loads((output / "source-observation.json").read_bytes())
    assert source_observation["dependency_changes"] == ["package.json"]
    assert source_observation["scope_verdict"] == "review-required"
    assert "authored-dependency-edit" in (output / "candidate.diff").read_text()
    envelope = observer.export_envelope(output, result)
    assert "scratch-projection.json" in {row["path"] for row in envelope["files"]}


def test_projection_records_dependency_root_links_without_following_them(tmp_path):
    source = tmp_path / "candidate"
    source.mkdir()
    outside = tmp_path / "external-dependencies"
    outside.mkdir()
    (outside / "must-not-follow").write_text("outside source")
    (source / "node_modules").symlink_to(outside, target_is_directory=True)
    (source / "ordinary-link").symlink_to(outside, target_is_directory=True)
    receipt = observer.scratch_projection(source, tmp_path / "qualified-dependencies")
    assert receipt["omitted_dependency_roots"] == ["node_modules"]
    assert receipt["omitted_dependency_inventory"] == {"node_modules": {"symlink": str(outside)}}
    scratch = tmp_path / "scratch"
    shutil.copytree(source, scratch, symlinks=True, ignore=observer.omit_scratch_dependencies)
    assert not (scratch / "node_modules").exists()
    assert os.readlink(scratch / "ordinary-link") == str(outside)
    assert (outside / "must-not-follow").read_text() == "outside source"
    assert os.readlink(source / "node_modules") == str(outside)


def test_oversized_projection_fails_without_truncating_the_receipt(tmp_path, monkeypatch):
    source = tmp_path / "candidate"
    (source / "node_modules/.cache").mkdir(parents=True)
    (source / "node_modules/.cache/generated").write_bytes(b"authored generated bytes")
    baseline = tmp_path / "baseline.json"
    baseline.write_text("{}")
    output = tmp_path / "observations"
    monkeypatch.setattr(observer, "MAX_MEMBER_BYTES", 1)
    result = observer.run(source, baseline, tmp_path / "dependencies", output, {}, environment)
    assert result["execution_status"] == "incomplete"
    assert result["commands"] == []
    assert "projection receipt exceeds evidence member budget" in result["error"]
    receipt = json.loads((output / "scratch-projection.json").read_bytes())
    assert receipt["omitted_dependency_inventory"]["node_modules/.cache/generated"]["sha256"] == (
        hashlib.sha256(b"authored generated bytes").hexdigest()
    )
    with pytest.raises(ValueError, match="budget") as error:
        observer.export_envelope(output, result)
    failure = observer.export_failure(output, result, error.value)
    assert failure["execution_status"] == "incomplete"
    assert failure["full_artifact_export_retained"] is False
    assert failure["summary"]["scratch_projection"] == result["scratch_projection"]
