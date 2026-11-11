"""Guest-only built-candidate observer; retained raw evidence is not a quality verdict.

Run through solver-isolated-v1, with the reviewed baseline and operator assets
outside the candidate tree. Host tests execute only authored fake tools.
"""

from __future__ import annotations

import argparse
import base64
import difflib
import hashlib
import importlib.util
import io
import json
import os
import platform
import re
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

MAX_EVIDENCE_BYTES = 16 * 1024 * 1024
MAX_ZIP_BYTES = 650 * 1024
MAX_MEMBER_BYTES = 4 * 1024 * 1024
MAX_EVIDENCE_FILES = 256


def load_environment(path):
    spec = importlib.util.spec_from_file_location("operational_metrics_environment", path)
    if not spec or not spec.loader:
        raise ValueError("explicit trusted environment helper is required")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def source_observation(baseline, candidate, inventory):
    """Inventory + complete textual changes; scope flags supplement source review."""
    inventory_only = baseline.is_file()
    before = json.loads(baseline.read_bytes()) if inventory_only else inventory(baseline)
    after = inventory(candidate)
    names = sorted(set(before) | set(after))
    changes, patches = [], []
    for name in names:
        if name.startswith(".git/") or before.get(name) == after.get(name):
            continue
        changes.append({"path": name, "before": before.get(name), "after": after.get(name)})
        if any("symlink" in row for row in (before.get(name, {}), after.get(name, {}))):
            patches.append(f"Symlink change: {name}\n")
            continue
        if inventory_only:
            continue
        old, new = baseline / name, candidate / name
        left = old.read_bytes() if old.is_file() else b""
        right = new.read_bytes() if new.is_file() else b""
        try:
            patches.extend(
                difflib.unified_diff(
                    left.decode().splitlines(keepends=True),
                    right.decode().splitlines(keepends=True),
                    fromfile=f"baseline/{name}",
                    tofile=f"candidate/{name}",
                )
            )
        except UnicodeDecodeError:
            patches.append(f"Binary change: {name}\n")
    # Conservative triage only. Frontend code can itself write, import helpers,
    # or mislead; browser network/store checks and source review remain required.
    outside = [row["path"] for row in changes if not row["path"].startswith(("frontend/", "docs/"))]
    dependency = [
        row["path"]
        for row in changes
        if Path(row["path"]).name
        in {"package.json", "pnpm-lock.yaml", "package-lock.json", "yarn.lock"}
    ]
    return {
        "changes": changes,
        "outside_frontend_or_docs": outside,
        "dependency_changes": dependency,
        "scope_verdict": "review-required",
        "baseline_bytes_available": not inventory_only,
        "source_diff_authority": "parent repair artifact changes.patch and source/candidate"
        if inventory_only
        else "candidate.diff",
        "baseline_inventory_sha256": digest(json.dumps(before, sort_keys=True).encode()),
        "candidate_inventory_sha256": digest(json.dumps(after, sort_keys=True).encode()),
    }, "".join(patches)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def evidence_manifest(directory):
    rows = []
    for item in sorted(directory.rglob("*")):
        if item.is_symlink():
            raise ValueError("evidence must not contain symlinks")
        if item.is_file() and not item.name.endswith(".trace.zip"):
            data = item.read_bytes()
            rows.append(
                {
                    "path": item.relative_to(directory).as_posix(),
                    "bytes": len(data),
                    "sha256": digest(data),
                }
            )
    if len(rows) > MAX_EVIDENCE_FILES or sum(row["bytes"] for row in rows) > MAX_EVIDENCE_BYTES:
        raise ValueError("raw evidence exceeds observer collection budget; preserve failure files")
    return rows


def run(source, baseline, dependencies, output, assets, environment, timeout=570):
    if output.exists() or output.is_relative_to(source) or output.is_relative_to(baseline):
        raise ValueError("fresh evidence path outside source/baseline is required")
    output.mkdir(parents=True)
    before = environment.inventory(source)
    result = {
        "schema_version": 1,
        "execution_status": "incomplete",
        "semantic_verdict": "unassessed",
        "commands": [],
        "evidence_directory": str(output),
    }
    source_result, patch = source_observation(baseline, source, environment.inventory)
    (output / "source-observation.json").write_text(json.dumps(source_result, indent=2))
    (output / "candidate.diff").write_text(patch)
    for name, data in assets.items():
        (output / name).write_bytes(data)
    deadline = time.monotonic() + timeout
    try:
        with tempfile.TemporaryDirectory(prefix="operational-metrics-downstream-") as temporary:
            root = Path(temporary)
            project = root / "projects/task"
            shutil.copytree(source, project, symlinks=True)
            environment.bridge_dependencies(project, dependencies)
            env = environment.application_environment(root)
            commands = [
                ["pnpm", "build"],
                [
                    "node",
                    str(output / "observe.mjs"),
                    str(output / "contract.json"),
                    str(output / "driver.json"),
                    str(output / "fixture.json"),
                    str(output / "browser"),
                ],
            ]
            for index, argv in enumerate(commands):
                logs = output / f"command-{index}"
                logs.mkdir()
                command, _stdout, _stderr = environment.execute(
                    argv, project, env, deadline - time.monotonic(), 4 * 1024 * 1024, logs
                )
                command["argv"] = argv
                result["commands"].append(command)
                if command["exit_code"] != 0 or not command["output_complete"]:
                    break
            after = environment.inventory(project)
            result["build_changed_candidate_inputs"] = environment.changed(before, after)
            browser_file = output / "browser/observation.json"
            if browser_file.is_file():
                observation = json.loads(browser_file.read_bytes())
                result["browser_execution_complete"] = observation["execution_complete"]
                result["store_unchanged"] = observation.get("store", {}).get("unchanged")
                result["regression_api_unchanged"] = observation.get("api", {}).get(
                    "regression_unchanged"
                )
            if (
                len(result["commands"]) == 2
                and all(
                    row["exit_code"] == 0 and row["output_complete"] for row in result["commands"]
                )
                and result.get("browser_execution_complete") is True
                and not result["build_changed_candidate_inputs"]
            ):
                result["execution_status"] = "observed"
    except (OSError, ValueError, KeyError) as error:
        result["error"] = str(error)
    result["candidate_source_changes"] = environment.changed(before, environment.inventory(source))
    if result["candidate_source_changes"]:
        result["execution_status"] = "incomplete"
    try:
        result["files"] = evidence_manifest(output)
    except ValueError as error:
        result["execution_status"] = "incomplete"
        result["error"] = str(error)
    (output / "result.json").write_text(json.dumps(result, indent=2))
    return result


def archive_name(name, seen):
    if (
        not re.fullmatch(r"[A-Za-z0-9_./-]+", name)
        or name.startswith("/")
        or any(part in {"", ".", ".."} for part in name.split("/"))
    ):
        raise ValueError("unsafe evidence archive member")
    if name.casefold() in seen:
        raise ValueError("evidence path case alias")
    seen.add(name.casefold())
    return name


def export_envelope(directory, result):
    """Encode retained observer artifacts within the native stdout budget."""
    files, omitted = [], []
    names, total = set(), 0
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise ValueError("evidence symlink is forbidden")
            if not path.is_file():
                continue
            name = archive_name(path.relative_to(directory).as_posix(), names)
            if path.stat().st_mode & 0o111:
                raise ValueError("executable evidence member is forbidden")
            contents = path.read_bytes()
            row = {"path": name, "bytes": len(contents), "sha256": digest(contents)}
            if name.endswith(".trace.zip"):
                omitted.append(
                    {
                        **row,
                        "reason": "diagnostic browser trace excluded from bounded native export",
                    }
                )
                continue
            total += len(contents)
            if (
                len(contents) > MAX_MEMBER_BYTES
                or total > MAX_EVIDENCE_BYTES
                or len(files) >= MAX_EVIDENCE_FILES
            ):
                raise ValueError("evidence member/total/count budget exceeded")
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, contents, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
            files.append(row)
    zipped = data.getvalue()
    if len(zipped) > MAX_ZIP_BYTES:
        raise ValueError(
            f"compressed evidence export budget exceeded: {len(zipped)} > {MAX_ZIP_BYTES}"
        )
    return {
        "schema_version": 1,
        "kind": "operational-metrics-downstream-envelope",
        "encoding": "zip+base64",
        "sha256": digest(zipped),
        "bytes": len(zipped),
        "base64": base64.b64encode(zipped).decode(),
        "files": files,
        "omitted_diagnostics": omitted,
        "summary": result,
    }


def export_failure(directory, result, error):
    """Retain bounded failure diagnostics, explicitly distinct from a full export."""
    files, total, omitted_traces = [], 0, 0
    for item in sorted(directory.rglob("*")):
        if item.is_symlink() or not item.is_file():
            continue
        size = item.stat().st_size
        total += size
        if item.name.endswith(".trace.zip"):
            omitted_traces += size
        files.append({"path": item.relative_to(directory).as_posix(), "bytes": size})
    summary = {
        key: value for key, value in result.items() if key not in {"files", "evidence_directory"}
    }
    summary["execution_status"] = "incomplete"
    summary["semantic_verdict"] = "unassessed"
    browser_path = directory / "browser/observation.json"
    browser = {"retained": False, "reason": "browser observation file unavailable"}
    if browser_path.is_file() and not browser_path.is_symlink():
        size = browser_path.stat().st_size
        if size <= 128 * 1024:
            try:
                browser = {
                    "retained": True,
                    "raw_observation": json.loads(browser_path.read_bytes()),
                }
            except (OSError, ValueError) as parse_error:
                browser = {"retained": False, "reason": str(parse_error)}
        else:
            browser = {
                "retained": False,
                "reason": "browser observation exceeds 128-KiB failure diagnostic budget",
                "bytes": size,
            }
    return {
        "schema_version": 1,
        "kind": "operational-metrics-downstream-export-failure",
        "execution_status": "incomplete",
        "semantic_verdict": "unassessed",
        "error": str(error),
        "full_artifact_export_retained": False,
        "summary": summary,
        "diagnostics": {
            "file_count": len(files),
            "total_bytes": total,
            "omitted_trace_bytes": omitted_traces,
            "files": files[:MAX_EVIDENCE_FILES],
            "file_diagnostics_omitted": max(0, len(files) - MAX_EVIDENCE_FILES),
            "browser": browser,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("/workspace/task"))
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--dependencies", type=Path, default=Path("/workspace/node_modules"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--environment-helper", type=Path, required=True)
    args = parser.parse_args()
    if (
        os.getuid() != 61000
        or platform.system() != "Linux"
        or Path.cwd() != Path("/workspace/task")
    ):
        raise RuntimeError("requires the explicit solver-isolated-v1 guest route")
    environment = load_environment(args.environment_helper)
    assets = {
        name: (args.assets / name).read_bytes()
        for name in (
            "observe.mjs",
            "scenarios.mjs",
            "contract.json",
            "driver.json",
            "fixture.json",
            "original-request.txt",
        )
    }
    result = run(
        args.source.resolve(),
        args.baseline.resolve(),
        args.dependencies.resolve(),
        args.output.resolve(),
        assets,
        environment,
    )
    try:
        envelope = export_envelope(args.output.resolve(), result)
    except ValueError as error:
        print(json.dumps(export_failure(args.output.resolve(), result, error), sort_keys=True))
        raise SystemExit(1) from error
    print(json.dumps(envelope, sort_keys=True))
    raise SystemExit(0 if result["execution_status"] == "observed" else 1)


if __name__ == "__main__":
    main()
