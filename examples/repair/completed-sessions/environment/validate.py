"""VM-only ordinary AgentMonitor validation; stdout JSON, complete raw logs on stderr.

Stage this trusted wrapper outside solver task source, or inline its bytes with
python3 -I -c. It runs imported project commands only inside the authorized VM.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from pathlib import Path

LANES = {
    "checks": [
        ["pnpm", "lint"],
        ["pnpm", "exec", "tsc", "--noEmit"],
        ["pnpm", "frontend:check"],
    ],
    "root-tests": [["pnpm", "build"], ["pnpm", "test"]],
    "frontend-tests": [["pnpm", "frontend:test", "--reporter=json", "--outputFile={report}"]],
}
RAW_LIMIT = 700 * 1024


def inventory(root):
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if name != "node_modules"]
        for name in files:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if relative == ".git/index":
                continue
            if path.is_symlink() or not path.is_file():
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


def run_lane(lane, source, dependencies, timeout=570):
    before = inventory(source)
    deadline = time.monotonic() + timeout
    observations = {"lane": lane, "commands": [], "status": "failed"}
    raw_remaining = RAW_LIMIT
    with tempfile.TemporaryDirectory(prefix="ordinary-validation-") as temporary:
        root = Path(temporary)
        project = root / "projects" / source.name
        shutil.copytree(source, project)
        bridge_dependencies(project, dependencies)
        for name in ("home", "codex-home", "claude-home", "empty-skills", "tmp", "logs"):
            (root / name).mkdir()
        env = dict(os.environ)
        env.update(
            {
                "HOME": str(root / "home"),
                "CODEX_HOME": str(root / "codex-home"),
                "AGENTMONITOR_CLAUDE_DIR": str(root / "claude-home"),
                "AGENTMONITOR_SKILL_CATALOG_DIRS": str(root / "empty-skills"),
                "AGENTMONITOR_DB_PATH": str(root / "application.db"),
                "AGENTMONITOR_PROJECTS_DIR": str(root / "projects"),
                "TMPDIR": str(root / "tmp"),
                "TMP": str(root / "tmp"),
                "TEMP": str(root / "tmp"),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "NO_COLOR": "1",
                "FORCE_COLOR": "0",
                "CI": "1",
            }
        )
        report = root / "vitest.json"
        for template in LANES[lane]:
            argv = [part.replace("{report}", str(report)) for part in template]
            command_env = dict(env)
            if template == ["pnpm", "test"]:
                # Pin only the reporter; preserve the ordinary script and all its
                # test discovery, private-temp cleanup and built-product checks.
                command_env["NODE_OPTIONS"] = "--test-reporter=tap"
            try:
                result, stdout, stderr = execute(
                    argv,
                    project,
                    command_env,
                    deadline - time.monotonic(),
                    max(1, raw_remaining),
                    root / "logs",
                )
            except OSError as error:
                result = {
                    "exit_code": None,
                    "timed_out": False,
                    "output_complete": False,
                    "execution_error": type(error).__name__,
                }
                stdout, stderr = b"", b""
            shown = b"\nCOMMAND " + json.dumps(template).encode() + b"\nSTDOUT\n" + stdout
            shown += b"\nSTDERR\n" + stderr + b"\nEND COMMAND\n"
            retained = shown[: max(0, raw_remaining)]
            sys.stderr.buffer.write(retained)
            sys.stderr.buffer.flush()
            raw_remaining -= len(retained)
            if len(retained) != len(shown):
                result["output_complete"] = False
            result["argv"] = template
            if result["output_complete"] and template == ["pnpm", "test"]:
                try:
                    result["tests"] = parse_tap(stdout.decode(errors="replace"))
                except ValueError as error:
                    result["parse_error"] = str(error)
            if result["output_complete"] and lane == "frontend-tests":
                try:
                    result["tests"] = parse_vitest(json.loads(report.read_bytes()), project)
                except (OSError, ValueError, KeyError, TypeError) as error:
                    result["parse_error"] = type(error).__name__
            observations["commands"].append(result)
            if not result["output_complete"]:
                break
        after = inventory(project)
        observations["existing_source_changes"] = changed(before, after)
        generated = {name: value for name, value in after.items() if name not in before}
        observations["generated_artifacts"] = {
            "scope": "scratch repository excluding dependency bridges and .git/index",
            "paths": sorted(generated),
            "count": len(generated),
            "total_bytes": sum(row["size"] for row in generated.values()),
            "max_bytes": max((row["size"] for row in generated.values()), default=0),
        }
    observations["captured_source_changes"] = changed(before, inventory(source))
    if (
        len(observations["commands"]) == len(LANES[lane])
        and all(
            row["exit_code"] == 0 and row["output_complete"] and "parse_error" not in row
            for row in observations["commands"]
        )
        and not observations["existing_source_changes"]
        and not observations["captured_source_changes"]
    ):
        observations["status"] = "passed"
    return observations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lane", choices=LANES)
    parser.add_argument("--source", type=Path, default=Path.cwd())
    parser.add_argument("--dependencies", type=Path, default=Path("/workspace/node_modules"))
    args = parser.parse_args()
    try:
        result = run_lane(args.lane, args.source.resolve(), args.dependencies.resolve())
    except (OSError, ValueError) as error:
        result = {"lane": args.lane, "status": "incomplete", "error": type(error).__name__}
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
