"""Host-side byte packaging only; app/browser execution is guest-only."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile
from pathlib import Path

from skilleval import repair, runner

ROOT = Path(__file__).resolve().parent
BOOTSTRAP = """import base64, io, json, os, platform, runpy, sys, tempfile, zipfile
from pathlib import Path
if os.getuid() != 61000 or platform.system() != "Linux" or Path.cwd() != Path("/workspace/task"):
    raise RuntimeError("requires the explicit solver-isolated-v1 guest route")
with tempfile.TemporaryDirectory(prefix="downstream-operator-") as temporary:
    root = Path(temporary)
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(sys.argv[1], validate=True))) as archive:
        for item in archive.infolist():
            if "/" in item.filename or item.filename in {"", ".", ".."}:
                raise ValueError("unexpected trusted asset path")
            (root / item.filename).write_bytes(archive.read(item))
    sys.argv = [str(root / "validate.py"), "--source", "/workspace/task",
        "--baseline", str(root / "baseline-inventory.json"),
        "--output", str(root / "observations"), "--assets", str(root),
        "--environment-helper", str(root / "environment.py")]
    runpy.run_path(str(root / "validate.py"), run_name="__main__")
"""


def lane_argv(
    driver: Path,
    baseline_bundle: Path,
    baseline_digest: str,
    *,
    original_request: str | None = None,
) -> tuple[str, ...]:
    """Freeze trusted assets/independent source inventory into an isolated lane argv.

    Driver is an operator-reviewed affordance plan, never generated-spec checks.
    The original baseline snapshot is verified before its inventory is packaged.
    Parent repair artifacts retain baseline bytes and the complete candidate diff.
    """
    _, files = runner._snapshot_files(baseline_bundle, baseline_digest)
    inventory = {
        name: {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size": len(raw),
            "executable": executable,
        }
        for name, raw, executable in files
        if not repair.ignored(name)
    }
    assets = {
        "baseline-inventory.json": json.dumps(inventory, sort_keys=True).encode(),
        "validate.py": (ROOT / "validate.py").read_bytes(),
        "environment.py": (ROOT.parent / "environment/validate.py").read_bytes(),
        "observe.mjs": (ROOT / "observe.mjs").read_bytes(),
        "scenarios.mjs": (ROOT / "scenarios.mjs").read_bytes(),
        "contract.json": json.dumps(
            {
                "api_fixture": json.loads(
                    (ROOT.parent / "controls/downstream-contract.json").read_bytes()
                )["api_fixture"]
            },
            sort_keys=True,
        ).encode(),
        "driver.json": json.dumps(
            {
                key: value
                for key, value in json.loads(driver.read_bytes()).items()
                if key
                in {
                    "schema_version",
                    "enter",
                    "away",
                    "return",
                    "refresh",
                    "retry",
                    "inspect",
                    "range",
                    "range_return",
                    "range_windows",
                    "keyboard",
                }
            },
            sort_keys=True,
        ).encode(),
        "fixture.json": (ROOT / "metrics-fixture.json").read_bytes(),
        "original-request.txt": (
            original_request.encode()
            if original_request is not None
            else (ROOT.parent / "prompt.txt").read_bytes()
        ),
    }
    # Parse only as data on the host. No captured module is imported/executed.
    if not isinstance(json.loads(assets["driver.json"]), dict):
        raise ValueError("operator driver must be a JSON object")
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, contents in assets.items():
            archive.writestr(name, contents)
    encoded = base64.b64encode(data.getvalue()).decode()
    if len(encoded) > 120 * 1024:
        raise ValueError("trusted asset argv exceeds Linux single-argument budget")
    return "python3", "-I", "-c", BOOTSTRAP, encoded
