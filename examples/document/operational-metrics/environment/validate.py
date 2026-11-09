"""Guest-only ordinary checks and built-console browser qualification.

Transport/parser helpers are reused from the completed-sessions environment.
Run only through solver-isolated-v1; host tests use authored executables.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import zlib
from contextlib import suppress
from pathlib import Path

RAW_LIMIT = 700 * 1024
STDERR_LIMIT = 1024 * 1024
# Native run default archive budget and exported evidence per-file ceiling.
MAX_FILES = 10_000
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024
LANES = {
    "checks": [["pnpm", "lint"], ["pnpm", "exec", "tsc", "--noEmit"], ["pnpm", "frontend:check"]],
    "root-tests": [["pnpm", "build"], ["pnpm", "test"]],
    "frontend-tests": [["pnpm", "frontend:test", "--reporter=json", "--outputFile={report}"]],
    "metrics-tests": [
        [
            "node",
            "--import",
            "tsx",
            "--test",
            "--test-reporter=tap",
            "tests/otel-metrics-store.test.ts",
            "tests/otel-metrics-classify.test.ts",
            "tests/otel-metrics-retry-upgrade.test.ts",
            "tests/cli-ops-metrics.test.ts",
        ]
    ],
    "browser": [["pnpm", "build"], ["node", "{browser}", "{fixture}"]],
}


def inventory(root):
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if name != "node_modules"]
        links = [name for name in dirs if (Path(directory) / name).is_symlink()]
        dirs[:] = [name for name in dirs if name not in links]
        for name in [*files, *links]:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if relative == ".git/index":
                continue
            if path.is_symlink():
                # Record support-state links without following them outside the
                # disposable tree (Codex creates temporary executable aliases).
                result[relative] = {"symlink": os.readlink(path)}
                continue
            if not path.is_file():
                raise ValueError("source inventory contains a nonregular file")
            data = path.read_bytes()
            result[relative] = {
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "executable": bool(path.stat().st_mode & 0o111),
            }
    return result


def changed(before, after):
    return sorted(name for name in before if before[name] != after.get(name))


def parse_tap(text):
    summaries = {}
    for name in ("tests", "suites", "pass", "fail", "cancelled", "skipped", "todo"):
        matches = re.findall(rf"^# {name} (\d+)\s*$", text, re.MULTILINE)
        if len(matches) != 1:
            raise ValueError("Node TAP requires one complete summary")
        summaries[name] = int(matches[0])
    if summaries["tests"] == 0:
        raise ValueError("Node TAP reported zero tests")
    parents = {}
    identities = []
    for line in text.splitlines():
        header = re.match(r"^(\s*)# Subtest: (.+)$", line)
        if header:
            depth = len(header[1])
            parents = {key: value for key, value in parents.items() if key < depth}
            parents[depth] = header[2]
        result = re.match(r"^(\s*)(not ok|ok) \d+ - (.*?)(?: # (SKIP|TODO)(?: (.*))?)?$", line)
        if result:
            depth = len(result[1])
            identity = [parents[key] for key in sorted(parents) if key < depth]
            identity.append(result[3])
            identities.append(
                {
                    "path": identity,
                    "status": result[2],
                    "directive": result[4],
                    "reason": result[5],
                }
            )
    if len(identities) != summaries["tests"] + summaries["suites"]:
        raise ValueError("Node TAP lacks complete test identities")
    return {
        "counts": summaries,
        "identities": identities,
        "failures": [row for row in identities if row["status"] == "not ok"],
        "skips": [row for row in identities if row["directive"] == "SKIP"],
    }


def parse_vitest(report, project):
    if not isinstance(report, dict) or not isinstance(report.get("testResults"), list):
        raise ValueError("Vitest JSON report is missing")
    counts = {}
    for name in ("numTotalTests", "numPassedTests", "numFailedTests", "numPendingTests"):
        value = report.get(name)
        if type(value) is not int or value < 0:
            raise ValueError("Vitest report lacks numeric counts")
        counts[name] = value
    if counts["numTotalTests"] == 0:
        raise ValueError("Vitest reported zero tests")
    identities = []
    for suite in report["testResults"]:
        name = Path(suite["name"])
        file = (
            name.resolve().relative_to(project.resolve()).as_posix()
            if name.is_absolute()
            else name.as_posix()
        )
        for assertion in suite["assertionResults"]:
            status = assertion["status"]
            if status not in {"passed", "failed", "pending", "skipped", "todo", "disabled"}:
                raise ValueError("unrecognized Vitest assertion status")
            identities.append({"file": file, "name": assertion["fullName"], "status": status})
    if len(identities) != counts["numTotalTests"]:
        raise ValueError("Vitest lacks complete test identities")
    for status, key in (("passed", "numPassedTests"), ("failed", "numFailedTests")):
        if sum(row["status"] == status for row in identities) != counts[key]:
            raise ValueError("Vitest assertion counts disagree")
    identities.sort(key=lambda row: (row["file"], row["name"], row["status"]))
    return {
        "counts": counts,
        "identities": identities,
        "failures": [row for row in identities if row["status"] == "failed"],
        "skips": [
            row for row in identities if row["status"] in {"pending", "skipped", "todo", "disabled"}
        ],
    }


def bridge_dependencies(project, dependencies):
    if not dependencies.is_dir() or not (dependencies / ".bin").is_dir():
        raise ValueError("qualified dependency directory is missing")
    # Real writable directories support the ordinary built CLI's temporary bin
    # fixtures. Their entries resolve to the same read-only installed packages.
    for target in (project / "node_modules", project / "frontend/node_modules"):
        if target.exists() and any(target.iterdir()):
            raise ValueError("task unexpectedly contains installed dependencies")
        target.mkdir(parents=True, exist_ok=True)
        for item in dependencies.iterdir():
            if item.name == ".bin":
                bins = target / ".bin"
                bins.mkdir()
                for tool in item.iterdir():
                    (bins / tool.name).symlink_to(tool)
            else:
                (target / item.name).symlink_to(item, target_is_directory=item.is_dir())


def execute(argv, project, env, timeout, raw_budget, log_directory):
    out_path, err_path = log_directory / "stdout", log_directory / "stderr"

    with out_path.open("wb") as out, err_path.open("wb") as err:
        process = subprocess.Popen(
            argv,
            cwd=project,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            start_new_session=True,
        )
        timed_out = False
        try:
            process.wait(timeout=max(0.1, timeout))
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait()
    stdout, stderr = out_path.read_bytes(), err_path.read_bytes()
    complete = not timed_out and len(stdout) + len(stderr) < raw_budget
    return (
        {"exit_code": process.returncode, "timed_out": timed_out, "output_complete": complete},
        stdout,
        stderr,
    )


def application_environment(root):
    # Neither provider configuration nor a host application HOME is an input.
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AGENTMONITOR_")
        and not key.endswith(("API_KEY", "ACCESS_TOKEN", "AUTH_TOKEN"))
        and key not in {"NODE_OPTIONS", "PORTLESS_URL"}
    }
    for name in ("home", "codex-home", "claude-home", "empty-skills", "tmp", "logs"):
        (root / name).mkdir()
    env.update(
        HOME=str(root / "home"),
        CODEX_HOME=str(root / "codex-home"),
        AGENTMONITOR_CLAUDE_DIR=str(root / "claude-home"),
        AGENTMONITOR_SKILL_CATALOG_DIRS=str(root / "empty-skills"),
        AGENTMONITOR_DB_PATH=str(root / "application.db"),
        AGENTMONITOR_PROJECTS_DIR=str(root / "projects"),
        AGENTMONITOR_GIT_POLL_INTERVAL_MS="0",
        AGENTMONITOR_GITHUB_LOOKUPS="false",
        TMPDIR=str(root / "tmp"),
        TMP=str(root / "tmp"),
        TEMP=str(root / "tmp"),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD="1",
        CI="1",
        NO_COLOR="1",
        FORCE_COLOR="0",
    )
    return env


def retain_logs(argv, stdout, stderr, remaining):
    shown = b"\nCOMMAND " + json.dumps(argv).encode() + b"\nSTDOUT\n" + stdout
    shown += b"\nSTDERR\n" + stderr + b"\nEND COMMAND\n"
    retained = shown[: max(0, remaining)]
    sys.stderr.buffer.write(retained)
    sys.stderr.buffer.flush()
    return len(retained), len(retained) == len(shown)


def collection_budget(project, files):
    regular = {name: row["size"] for name, row in files.items() if "size" in row}
    links = [
        {"path": "task/" + name, "kind": "symlink", "target": row["symlink"]}
        for name, row in files.items()
        if "symlink" in row
    ]
    # Source-change inventories intentionally ignore Git's index because ordinary
    # Git reads can refresh it. The native artifact collector still receives it.
    index = project / ".git/index"
    if index.is_file():
        regular[".git/index"] = index.stat().st_size
    sizes = list(regular.values())
    # Same conservative header/padding allowance as guest_runner._archive, for
    # this source tree only. Outer collection must still admit home/evidence.
    headers = sum(
        max(4096, len(tarfile.TarInfo("task/" + name).tobuf(format=tarfile.PAX_FORMAT)) + 511)
        for name in regular
    )
    archive_upper_bound = sum(sizes) + len(json.dumps(links, sort_keys=True).encode())
    archive_upper_bound += headers + 4096 + 10240
    entries = len(regular) + len(links)
    return {
        "scope": "complete final scratch source including Git, excluding injected node_modules",
        "files": entries,
        "total_bytes": sum(sizes),
        "max_bytes": max(sizes, default=0),
        "archive_upper_bound_bytes": archive_upper_bound,
        "native_default_archive_limit": MAX_TOTAL_BYTES,
        "exported_evidence_file_limit": MAX_FILE_BYTES,
        "files_within_limit": entries < MAX_FILES,
        "total_within_limit": archive_upper_bound <= MAX_TOTAL_BYTES,
        "largest_within_limit": max(sizes, default=0) <= MAX_FILE_BYTES,
    }


def browser_observation(text):
    result = json.loads(text.strip().splitlines()[-1])
    screenshot = result["screenshot"]
    data = base64.b64decode(screenshot["base64"], validate=True)
    if (
        not valid_png(data)
        or len(data) != screenshot["bytes"]
        or hashlib.sha256(data).hexdigest() != screenshot["sha256"]
    ):
        raise ValueError("browser screenshot is missing or inconsistent")
    if (
        result.get("uid") != 61000
        or result.get("page_errors") != []
        or result.get("database_is_synthetic") is not True
        or result.get("browser_same_origin_metrics") is not True
        or result.get("empty_control") is not True
    ):
        raise ValueError("browser controls are incomplete")
    result["screenshot_valid_png"] = True
    return result


def valid_png(data):
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    offset, dimensions, pixels = 8, None, bytearray()
    while offset + 12 <= len(data):
        length = int.from_bytes(data[offset : offset + 4])
        chunk = data[offset + 4 : offset + 8]
        end = offset + 8 + length
        if end + 4 > len(data):
            return False
        contents = data[offset + 8 : end]
        crc = int.from_bytes(data[end : end + 4])
        if zlib.crc32(chunk + contents) != crc:
            return False
        if chunk == b"IHDR":
            if dimensions is not None or length != 13 or offset != 8:
                return False
            dimensions = (int.from_bytes(contents[:4]), int.from_bytes(contents[4:8]))
        elif chunk == b"IDAT":
            pixels.extend(contents)
        elif chunk == b"IEND":
            try:
                return (
                    length == 0
                    and end + 4 == len(data)
                    and dimensions is not None
                    and all(dimensions)
                    and bool(zlib.decompress(pixels))
                )
            except zlib.error:
                return False
        offset = end + 4
    return False


def stable_summary(observed):
    """Exact-comparison surface; raw details and image remain in stderr evidence."""
    summary = {
        key: observed[key]
        for key in ("lane", "status", "existing_source_changes", "captured_source_changes")
        if key in observed
    }
    if "error" in observed:
        summary["error"] = observed["error"]
    summary["commands"] = []
    for command in observed.get("commands", []):
        row = {
            key: command[key]
            for key in ("argv", "exit_code", "timed_out", "output_complete", "parse_error", "tests")
            if key in command
        }
        if "browser" in command:
            browser = command["browser"]
            row["browser"] = {
                key: browser[key]
                for key in (
                    "browser",
                    "uid",
                    "database_is_synthetic",
                    "schema",
                    "fixture_rows",
                    "metrics",
                    "empty_control",
                    "browser_same_origin_metrics",
                    "navigation",
                    "page_errors",
                    "screenshot_valid_png",
                )
            }
        summary["commands"].append(row)
    if "application_home" in observed:
        summary["application_home"] = {
            key: observed["application_home"][key]
            for key in ("outside_source", "database_outside_source")
        }
    if "collection_budget" in observed:
        summary["collection_budget"] = {
            key: observed["collection_budget"][key]
            for key in ("files_within_limit", "total_within_limit", "largest_within_limit")
        }
    return summary


def retain_observation(observed):
    data = b"\nFULL OBSERVATION\n" + json.dumps(observed, sort_keys=True).encode() + b"\n"
    available = max(0, STDERR_LIMIT - observed.get("retained_log_bytes", 0))
    complete = len(data) <= available
    sys.stderr.buffer.write(data[:available])
    sys.stderr.buffer.flush()
    return complete


def run_lane(lane, source, dependencies, *, browser=None, fixture=None, timeout=570):
    before = inventory(source)
    deadline = time.monotonic() + timeout
    observed = {"lane": lane, "commands": [], "status": "failed"}
    remaining = RAW_LIMIT
    with tempfile.TemporaryDirectory(prefix="operational-metrics-validation-") as temporary:
        root = Path(temporary)
        project = root / "projects/task"
        shutil.copytree(source, project, symlinks=True)
        bridge_dependencies(project, dependencies)
        env = application_environment(root)
        report = root / "vitest.json"
        if lane == "browser":
            if not browser or not fixture:
                raise ValueError("browser lane requires explicit trusted browser and fixture bytes")
            (root / "browser.mjs").write_bytes(browser)
            (root / "metrics.json").write_bytes(fixture)
        for template in LANES[lane]:
            substitutions = {
                "{report}": str(report),
                "{browser}": str(root / "browser.mjs"),
                "{fixture}": str(root / "metrics.json"),
            }
            argv = [
                substitutions.get(part, part.replace("{report}", str(report))) for part in template
            ]
            command_env = dict(env)
            if template == ["pnpm", "test"]:
                command_env["NODE_OPTIONS"] = "--test-reporter=tap"
            result, stdout, stderr = execute(
                argv,
                project,
                command_env,
                deadline - time.monotonic(),
                max(1, remaining),
                root / "logs",
            )
            retained, complete = retain_logs(template, stdout, stderr, remaining)
            remaining -= retained
            result["output_complete"] &= complete
            result["argv"] = template
            if result["output_complete"]:
                try:
                    if template == ["pnpm", "test"] or lane == "metrics-tests":
                        result["tests"] = parse_tap(stdout.decode(errors="replace"))
                    elif lane == "frontend-tests":
                        result["tests"] = parse_vitest(json.loads(report.read_bytes()), project)
                    elif template[0:2] == ["node", "{browser}"]:
                        result["browser"] = browser_observation(stdout.decode())
                except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
                    result["parse_error"] = str(error)
            observed["commands"].append(result)
            if not result["output_complete"]:
                break
        after = inventory(project)
        observed["application_home"] = {
            "outside_source": not (root / "home").is_relative_to(project),
            "database_outside_source": not (root / "application.db").is_relative_to(project),
            "codex_entries": sorted(item.name for item in (root / "codex-home").iterdir()),
        }
        observed["existing_source_changes"] = changed(before, after)
        generated = {name: value for name, value in after.items() if name not in before}
        observed["generated_artifacts"] = {
            "scope": "scratch source excluding dependencies and .git/index",
            "paths": sorted(generated),
            "count": len(generated),
            "total_bytes": sum(row.get("size", 0) for row in generated.values()),
            "max_bytes": max((row.get("size", 0) for row in generated.values()), default=0),
        }
        observed["collection_budget"] = collection_budget(project, after)
        observed["retained_log_bytes"] = RAW_LIMIT - remaining
    observed["captured_source_changes"] = changed(before, inventory(source))
    if (
        len(observed["commands"]) == len(LANES[lane])
        and all(
            row["exit_code"] == 0 and row["output_complete"] and "parse_error" not in row
            for row in observed["commands"]
        )
        and not observed["existing_source_changes"]
        and not observed["captured_source_changes"]
        and all(
            observed["collection_budget"][key]
            for key in ("files_within_limit", "total_within_limit", "largest_within_limit")
        )
    ):
        observed["status"] = "passed"
    return observed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lane", choices=LANES)
    parser.add_argument("--source", type=Path, default=Path.cwd())
    parser.add_argument("--dependencies", type=Path, default=Path("/workspace/node_modules"))
    parser.add_argument("--browser-base64")
    parser.add_argument("--fixture-base64")
    parser.add_argument(
        "--summary",
        action="store_true",
        help="emit deterministic stdout; retain full observations on stderr",
    )
    args = parser.parse_args()
    if (
        os.getuid() != 61000
        or platform.system() != "Linux"
        or Path.cwd() != Path("/workspace/task")
    ):
        raise RuntimeError("requires the explicit solver-isolated-v1 guest route")
    try:
        result = run_lane(
            args.lane,
            args.source.resolve(),
            args.dependencies.resolve(),
            browser=base64.b64decode(args.browser_base64, validate=True)
            if args.browser_base64
            else None,
            fixture=base64.b64decode(args.fixture_base64, validate=True)
            if args.fixture_base64
            else None,
        )
    except (OSError, ValueError) as error:
        result = {"lane": args.lane, "status": "incomplete", "error": str(error)}
    if args.summary:
        if not retain_observation(result):
            result["status"] = "incomplete"
            result["error"] = "full observations exceed the 1-MiB stderr retention limit"
        result = stable_summary(result)
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
