"""Guest-only artifact observations; specification meaning is assessed separately.

No candidate modules are imported or executed. Any Markdown filename beneath
docs/specs is allowed. Resource ceilings bound collection, not writing quality.
Questions, incorrect claims and unfinished prose intentionally look the same as
a completed specification here; the specification semantic profile distinguishes
them against the original request and pinned repository contracts.
"""

import json
import os
import sys
from pathlib import Path

MAX_FILE_BYTES = 1024 * 1024
MAX_ARTIFACTS = 128


def observe(root: Path) -> dict[str, bool]:
    result = {"candidate_found": False, "artifacts_readable": False}
    directory = root / "docs" / "specs"
    if (root / "docs").is_symlink() or directory.is_symlink() or not directory.is_dir():
        return result
    readable = True
    candidates = 0
    for parent, directories, files in os.walk(directory, followlinks=False):
        if any((Path(parent) / name).is_symlink() for name in directories):
            readable = False
        for name in files:
            artifact = Path(parent) / name
            if artifact.suffix.lower() not in {".md", ".markdown"}:
                continue
            result["candidate_found"] = True
            candidates += 1
            if candidates > MAX_ARTIFACTS:
                return result
            try:
                if artifact.is_symlink() or not artifact.is_file():
                    readable = False
                    continue
                with artifact.open("rb") as stream:
                    raw = stream.read(MAX_FILE_BYTES + 1)
                if len(raw) > MAX_FILE_BYTES or not raw.decode("utf-8-sig").strip():
                    readable = False
            except (OSError, UnicodeError):
                readable = False
    result["artifacts_readable"] = result["candidate_found"] and readable
    return result


def main() -> None:
    requests = json.load(sys.stdin)["requests"]
    if any(request.get("check") != "spec-artifact" for request in requests):
        raise ValueError("unknown operational-metrics document check")
    values = observe(Path(sys.argv[1]))
    print(json.dumps({"observations": [values for _ in requests]}))


if __name__ == "__main__":
    main()
