"""Pinned native skill inputs and conservative consultation observations.

No imported code runs here. A captured bundle is an experimental input, not an
installed host skill. Native discovery uses the supported CODEX_HOME/skills root
(inspected Codex source revision 1c0c45bfc372fb7d7d581508fdef5eef8235eab7,
codex-rs/ext/skills/src/host_roots.rs). Discovery still needs a live positive
control for the selected binary; packaging alone does not establish discovery.
"""

from __future__ import annotations

import hashlib
import os
import re
import shlex
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from skilleval import evidence
from skilleval.errors import SkillEvalError

_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_NOTICE = ".source-notices/ROOT_LICENSE"
_LEGACY_NOTICE = ".skilleval-notices/ROOT_LICENSE"


def _inventory(files: list[tuple[str, bytes, bool]]) -> list[dict[str, Any]]:
    return [
        {
            "path": name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
            "executable": executable,
        }
        for name, data, executable in sorted(files)
    ]


def _read_tree(root: Path) -> list[tuple[str, bytes, bool]]:
    try:
        files: list[tuple[str, bytes, bool]] = []
        total = 0
        with evidence._directory(root) as descriptor:
            for entry in evidence._walk(descriptor):
                total += len(entry[1])
                if len(files) >= evidence.MAX_FILES or total > evidence.MAX_TOTAL_BYTES:
                    raise SkillEvalError("treatment inventory limit exceeded")
                files.append(entry)
        aliases = [name.casefold() for name, _, _ in files]
        if len(set(aliases)) != len(aliases):
            raise SkillEvalError("case-aliased treatment paths are unsupported")
        return files
    except OSError:
        raise SkillEvalError("cannot read treatment input") from None


def _skill_name(body: bytes) -> str:
    try:
        text = body.decode("utf-8")
    except UnicodeError:
        raise SkillEvalError("skill metadata must be UTF-8") from None
    match = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", text, re.DOTALL)
    if match is None:
        raise SkillEvalError("skill requires native frontmatter")
    names = re.findall(r"^name:[ \t]*([^\r\n]+)$", match[1], re.MULTILINE)
    descriptions = re.findall(r"^description:[ \t]*(\S[^\r\n]*)$", match[1], re.MULTILINE)
    if len(names) != 1 or len(descriptions) != 1:
        raise SkillEvalError("skill requires one name and description")
    name: str = names[0].strip().strip("\"'")
    if _NAME.fullmatch(name) is None:
        raise SkillEvalError("unsupported native skill name")
    return name


def capture_skill(
    source_tree: Path,
    root_license: Path | None,
    destination: Path,
    *,
    source_url: str,
    source_revision: str,
) -> dict[str, Any]:
    """Capture one explicit skill tree and an optional, supplied root notice.

    All tree files, including references/scripts and existing notices, retain
    their bytes and executable flag. As with evidence snapshots, non-executable
    permission bits are normalized. Provenance belongs to the host bundle, never
    the solver prompt. No sibling path or permission/license claim is inferred.
    An omitted root notice is recorded as not-provided; bundled notices remain.
    """
    if not source_url.strip() or not source_revision.strip():
        raise SkillEvalError("explicit skill source and revision are required")
    files = _read_tree(source_tree)
    content = {name: data for name, data, _ in files}
    if "SKILL.md" not in content:
        raise SkillEvalError("selected skill tree requires SKILL.md")
    reserved = {Path(path).parts[0] for path in (_NOTICE, _LEGACY_NOTICE)}
    if any(Path(path.casefold()).parts[0] in reserved for path in content):
        raise SkillEvalError("selected skill uses a reserved notice directory")
    name = _skill_name(content["SKILL.md"])
    notice: bytes | None = None
    executable = False
    if root_license is not None:
        try:
            with evidence._directory(root_license.parent) as descriptor:
                notice, executable = evidence._read_file(descriptor, root_license.name)
        except OSError:
            raise SkillEvalError("cannot capture explicit root license") from None
        if not notice.strip():
            raise SkillEvalError("explicit root license is empty")
    if Path(os.path.abspath(destination)).is_relative_to(Path(os.path.abspath(source_tree))):
        raise SkillEvalError("skill capture destination must be outside source")

    def populate(_descriptor: int, writer: evidence._Writer) -> None:
        for relative, data, mode in files:
            writer.add(f"skills/{name}/{relative}", data, mode)
        if notice is not None:
            writer.add(f"skills/{name}/{_NOTICE}", notice, executable)

    return evidence._capture(
        source_tree,
        destination,
        "snapshot",
        {
            "purpose": "skill-treatment",
            "skill_name": name,
            "source_url": source_url,
            "source_revision": source_revision,
            "license_status": "provided" if notice is not None else "not-provided",
            "notice_path": _NOTICE,
            "discovery": "CODEX_HOME/skills",
            "permissions": "bytes-and-executable-flag",
        },
        populate,
    )


def _verified_skill(
    bundle: Path, expected_digest: str
) -> tuple[dict[str, Any], list[tuple[str, bytes, bool]]]:
    if re.fullmatch(r"[0-9a-f]{64}", expected_digest) is None:
        raise SkillEvalError("pinned skill manifest digest is required")
    manifest = evidence.verify(bundle, expected_digest)
    meta = manifest["meta"]
    name = meta.get("skill_name")
    # Old immutable captures keep their original name and bytes. Never rename a
    # pinned package during installation; neutral presentation needs a new capture.
    notice_path = meta.get("notice_path", _LEGACY_NOTICE)
    if (
        manifest["kind"] != "snapshot"
        or meta.get("purpose") != "skill-treatment"
        or not isinstance(name, str)
        or _NAME.fullmatch(name) is None
        or meta.get("license_status") not in {"provided", "not-provided"}
        or notice_path not in (_NOTICE, _LEGACY_NOTICE)
    ):
        raise SkillEvalError("bundle is not a supported captured skill")
    prefix = f"skills/{name}/"
    files = [entry for entry in _read_tree(bundle) if entry[0] != "manifest.json"]
    expected = manifest["files"]
    if _inventory(files) != expected:
        raise SkillEvalError("skill input changed after verification")
    content = {relative: data for relative, data, _ in files}
    if (
        any(not relative.startswith(prefix) for relative in content)
        or ((prefix + notice_path in content) != (meta["license_status"] == "provided"))
        or prefix + "SKILL.md" not in content
        or _skill_name(content[prefix + "SKILL.md"]) != name
    ):
        raise SkillEvalError("skill bundle inventory does not match its native name")
    return manifest, files


def install_skill(bundle: Path, expected_digest: str, codex_home: Path) -> dict[str, Any]:
    """Install verified input into a fresh isolated CODEX_HOME, never user HOME."""
    manifest, files = _verified_skill(bundle, expected_digest)
    temporary: Path | None = None
    try:
        _, codex_home = evidence._paths(bundle, codex_home)
        temporary = Path(tempfile.mkdtemp(prefix=".skilleval-skill-", dir=codex_home.parent))
        for relative, data, executable in files:
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o755 if executable else 0o644)
        if _inventory(_read_tree(temporary)) != manifest["files"]:
            raise SkillEvalError("installed skill inventory mismatch")
        evidence.verify(bundle, expected_digest)
        evidence._publish(temporary, codex_home)
        temporary = None
        return {
            "manifest_sha256": expected_digest,
            "skill_name": manifest["meta"]["skill_name"],
            "discovery": "CODEX_HOME/skills",
            "files": manifest["files"],
        }
    except OSError:
        raise SkillEvalError("cannot install captured skill") from None
    finally:
        if temporary is not None:
            shutil.rmtree(temporary)


def check_task_baseline(workspace: Path) -> dict[str, Any]:
    """Reject embedded skill/config inputs; retain all ordinary instructions.

    This checks the explicit task tree. Fresh guest HOME/system/plugin state is
    the runner's responsibility. It does not inspect or modify host user state.
    """
    files = _read_tree(workspace)
    for name, _, _ in files:
        parts = PurePosixPath(name).parts
        lowered = tuple(part.casefold() for part in parts)
        if (
            any(
                lowered[index : index + 2]
                in {(".agents", "skills"), (".codex", "skills"), (".claude", "skills")}
                for index in range(len(lowered) - 1)
            )
            or ".codex" in lowered
        ):
            raise SkillEvalError("task contains unsupported embedded skills or Codex configuration")
    inventory = _inventory(files)
    return {
        "task_sha256": hashlib.sha256(evidence._json_bytes(inventory)).hexdigest(),
        "files": inventory,
        "ordinary_instructions_preserved": True,
    }


def prepare_pair(
    workspace: Path,
    expected_task_digest: str,
    skill_bundle: Path,
    expected_skill_digest: str,
    destination: Path,
    *,
    prompt: bytes,
) -> dict[str, Any]:
    """Prepare matched task/prompt inputs; only separate skill availability varies.

    Receipt metadata remains at the pair root. Transfer only each arm's task,
    prompt, and isolated Codex home to its solver, never the pair receipt/bundle.
    Forced invocation is deliberately outside this natural-discovery operation.
    """
    identity = check_task_baseline(workspace)
    if identity["task_sha256"] != expected_task_digest:
        raise SkillEvalError("pinned task inventory changed")
    _verified_skill(skill_bundle, expected_skill_digest)
    files = _read_tree(workspace)
    if _inventory(files) != identity["files"]:
        raise SkillEvalError("task changed before pair preparation")
    temporary: Path | None = None
    try:
        _, destination = evidence._paths(workspace, destination)
        if destination.is_relative_to(skill_bundle.absolute()):
            raise SkillEvalError("pair destination must be outside skill bundle")
        temporary = Path(tempfile.mkdtemp(prefix=".skilleval-pair-", dir=destination.parent))
        for mode in ("absent", "available"):
            arm = temporary / mode
            (arm / "task").mkdir(parents=True)
            for relative, data, executable in files:
                target = arm / "task" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                target.chmod(0o755 if executable else 0o644)
            (arm / "prompt.txt").write_bytes(prompt)
            if mode == "available":
                install_skill(skill_bundle, expected_skill_digest, arm / "codex-home")
            else:
                (arm / "codex-home" / "skills").mkdir(parents=True)
        receipt = {
            "schema_version": 1,
            "task_sha256": expected_task_digest,
            "prompt_sha256": hashlib.sha256(prompt).hexdigest(),
            "skill_sha256": expected_skill_digest,
            "arms": {"absent": "absent", "available": "available"},
            "invocation": "natural-discovery",
        }
        (temporary / "treatment.json").write_bytes(evidence._json_bytes(receipt))
        if check_task_baseline(workspace) != identity:
            raise SkillEvalError("task changed during pair preparation")
        evidence.verify(skill_bundle, expected_skill_digest)
        evidence._publish(temporary, destination)
        temporary = None
        return receipt
    except OSError:
        raise SkillEvalError("cannot prepare matched treatment inputs") from None
    finally:
        if temporary is not None:
            shutil.rmtree(temporary)


def _read_command(command: str, skill_path: str) -> bool:
    """Recognize simple reads only; arbitrary scripts are deliberately unknown."""
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    if len(argv) == 3 and Path(argv[0]).name in {"sh", "bash", "zsh"} and argv[1] in {"-c", "-lc"}:
        return _read_command(argv[2], skill_path)
    # Full known content must also occur in the successful command output.
    if len(argv) in {2, 3} and Path(argv[0]).name == "cat":
        return argv[1:] in ([skill_path], ["--", skill_path])
    if len(argv) == 4 and Path(argv[0]).name == "sed" and argv[1] == "-n":
        return re.fullmatch(r"[0-9]+,(?:[0-9]+|\$)p", argv[2]) is not None and (
            argv[3] == skill_path
        )
    return False


def observe_consultation(
    events: list[dict[str, Any]],
    bundle: Path,
    expected_digest: str,
    skill_path: str,
) -> dict[str, Any]:
    """Record complete known skill content in successful native read output.

    An observation is evidence of content exposure, not semantic application or
    quality. Self-reports, named commands alone, failed commands, truncated reads,
    other tool schemas and missing telemetry stay unmeasured. This detector never
    turns a lack of matches into evidence that no consultation occurred.
    """
    manifest, files = _verified_skill(bundle, expected_digest)
    name = manifest["meta"]["skill_name"]
    if not skill_path.endswith(f"/skills/{name}/SKILL.md"):
        raise SkillEvalError("observed path must name the installed native skill")
    body = {relative: data for relative, data, _ in files}[f"skills/{name}/SKILL.md"]
    try:
        known = body.decode("utf-8")
    except UnicodeError:
        raise SkillEvalError("skill content must be UTF-8 for observation") from None
    observed = []
    for index, event in enumerate(events):
        item = event.get("item")
        if event.get("type") != "item.completed" or not isinstance(item, dict):
            continue
        command, output = item.get("command"), item.get("aggregated_output")
        if (
            item.get("type") == "command_execution"
            and item.get("status") == "completed"
            and type(item.get("exit_code")) is int
            and item["exit_code"] == 0
            and isinstance(command, str)
            and isinstance(output, str)
            and len(known.strip()) >= 64
            and known in output
            and _read_command(command, skill_path)
        ):
            observed.append({"event_index": index, "item_id": item.get("id")})
    return {
        "schema_version": 1,
        "skill_sha256": expected_digest,
        "consultation": "observed" if observed else "unmeasured",
        "content_exposure": "observed" if observed else "unknown",
        "detector": "native-completed-read-with-complete-pinned-content-v1",
        "observations": observed,
        "semantic_application": "unassessed",
        "absence_established": False,
    }


def request_observer(
    bundle: Path, expected_digest: str, skill_path: str
) -> Callable[[bytes], dict[str, Any]]:
    """Prepare a metadata-only detector for actual host-broker request bytes.

    This separates catalog exposure from body exposure. False only means this
    request did not match this conservative detector; it never proves absence of
    discovery/reading. No source content or request content is returned/stored.
    Diagnostics count matching strings by top-level surface; component matches
    do not promote a partial catalog or body match into an exposure observation.
    """
    manifest, files = _verified_skill(bundle, expected_digest)
    name = manifest["meta"]["skill_name"]
    if not skill_path.endswith(f"/skills/{name}/SKILL.md"):
        raise SkillEvalError("observed path must name the installed native skill")
    body = {relative: data for relative, data, _ in files}[f"skills/{name}/SKILL.md"].decode()
    # Capture only accepts a single-line description. Native YAML presentation
    # can differ for complex metadata; unsupported presentations stay unmatched.
    description_match = re.search(r"^description:[ \t]*(\S[^\r\n]*)$", body, re.MULTILINE)
    assert description_match is not None
    description = description_match[1].strip().strip("\"'")

    def observe(raw: bytes) -> dict[str, Any]:
        result: dict[str, Any] = {
            "skill_sha256": expected_digest,
            "skill_metadata_observed": None,
            "skill_body_observed": None,
            "detector": "decoded-native-request-pinned-skill-content-v1",
            "diagnostics_version": 1,
            "match_diagnostics": None,
            "absence_established": False,
        }
        try:
            value = evidence._parse_json(raw)
        except SkillEvalError:
            return result
        strings: list[str] = []
        diagnostics = {
            surface: {"string_count": 0, "name": 0, "description": 0, "path": 0, "body": 0}
            for surface in ("instructions", "input", "other")
        }
        needles = {"name": name, "description": description, "path": skill_path, "body": body}

        def collect(node: Any, surface: str = "other") -> None:
            if isinstance(node, str):
                strings.append(node)
                counts = diagnostics[surface]
                counts["string_count"] += 1
                for key, needle in needles.items():
                    counts[key] += int(needle in node)
            elif isinstance(node, dict):
                for child in node.values():
                    collect(child, surface)
            elif isinstance(node, list):
                for child in node:
                    collect(child, surface)

        try:
            if isinstance(value, dict):
                for key, child in value.items():
                    collect(child, key if key in {"instructions", "input"} else "other")
            else:
                collect(value)
        except RecursionError:
            return result
        result["match_diagnostics"] = diagnostics
        result["skill_metadata_observed"] = any(
            name in text and description in text and skill_path in text for text in strings
        )
        result["skill_body_observed"] = any(body in text for text in strings)
        return result

    return observe


def request_observation(
    raw: bytes, bundle: Path, expected_digest: str, skill_path: str
) -> dict[str, Any]:
    """Observe one request; use request_observer to reuse pinned detector input."""
    return request_observer(bundle, expected_digest, skill_path)(raw)
