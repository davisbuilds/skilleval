"""VM-only observation adapter for a public receipt-admission repair.

Newly authored fixtures exercise the real validate/validate_receipt, workflow,
source-hash and assess-controls functions. Task inspection is a fixture seam;
Docker replay returns controlled behavioral observations. Expected outcomes and
verdict computation are not part of this worker and never enter the guest.
"""

import copy
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, sys.argv[1])
from obench import repair_validation as rv


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def observe(request):
    scenario = request["scenario"]
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        task = root / "task"
        image = "sha256:" + "a" * 64
        checks = [{"id": "behavior-a", "bucket": "a"}, {"id": "behavior-b", "bucket": "b"}]
        info = {
            "schema": 1,
            "kind": "repair-task-inspection",
            "task": str(task),
            "task_binding": {"scheme": 4, "sha256": "b" * 64},
            "revision": {"oracle": "fixture"},
            "source_prefix": "impl",
            "checks": checks,
            "buckets": ["a", "b"],
            "quality_eligible": True,
        }
        controls = []
        source_failures = {}
        definitions = [
            ("baseline", "baseline", ["behavior-a", "behavior-b"]),
            ("good-a", "valid", []),
            ("good-b", "valid", []),
            ("bad-a", "invalid", ["behavior-a"]),
            ("bad-b", "invalid", ["behavior-b"]),
        ]
        for name, role, failures in definitions:
            source = task / "environment/app" if role == "baseline" else root / name
            source.mkdir(parents=True)
            (source / "impl").mkdir()
            (source / "impl/module.py").write_text(f"# fixture source {name}\n")
            controls.append(
                {"id": name, "role": role, "source": str(source), "must_fail": failures}
            )
            source_failures[str(source)] = failures
        controls_path = root / "controls.json"
        declaration = {
            "schema": 1,
            "contract_review": "Authored behavioral fixture; no Docker claim.",
            "project_check": "python3 -m unittest",
            "controls": controls,
        }
        write(controls_path, declaration)
        workflow_path = root / "workflow.json"
        evidence = {}
        for relative in (
            "agent/codex.txt",
            "agent/developer-workflow.json",
            "requests.jsonl",
            "gateway.jsonl",
        ):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("authored workflow fixture\n")
            evidence[relative] = digest(target)
        workflow = {
            "status": "passed",
            "cleanup_confirmed": True,
            "live_inference": False,
            "real_credentials": False,
            "actual_tool_mutation": True,
            "tool_result_returned": True,
            "final_response_present": True,
            "developer_workflows_passed": True,
            "runtime_image": image,
            "task": str(task),
            "task_binding": info["task_binding"],
            "project_check": declaration["project_check"],
            "probe_sha256": digest(rv.ROOT / "scripts/local/verify_repair_codex.py"),
            "validation_sha256": digest(Path(rv.__file__)),
            "evidence_sha256": evidence,
        }
        write(workflow_path, workflow)
        generation = ["saved"]
        active_info = copy.deepcopy(info)

        def replay(_task, source, _image):
            failures = source_failures[str(source)]
            rows = [
                {**check, "pass": check["id"] not in failures, "observation": generation[0]}
                for check in checks
            ]
            return {
                "task": copy.deepcopy(active_info),
                "image": image,
                "implementation": rv.implementation(),
                "source_sha256": rv.source_hashes(source, active_info),
                "solved": not failures,
                "grading": {
                    "score": 0 if failures else 1,
                    "buckets": {
                        "a": int("behavior-a" not in failures),
                        "b": int("behavior-b" not in failures),
                    },
                    "checks": rows,
                },
            }

        with (
            patch.object(rv, "inspect_task", side_effect=lambda _task: copy.deepcopy(active_info)),
            patch.object(rv, "replay", side_effect=replay),
        ):
            saved = rv.validate(task, controls_path, image, workflow_path)
            if saved["status"] != "passed":
                raise RuntimeError("fixture did not establish a valid control receipt")
            generation[0] = "fresh"
            if scenario == "forged-valid":
                source_failures[controls[1]["source"]] = ["behavior-a"]
            elif scenario == "forged-invalid":
                source_failures[controls[3]["source"]] = []
            elif scenario == "changed-id":
                declaration["controls"][1]["id"] = "renamed-valid"
                write(controls_path, declaration)
                saved["controls_sha256"] = digest(controls_path)
            elif scenario == "changed-target":
                declaration["controls"][0]["must_fail"] = ["behavior-a"]
                write(controls_path, declaration)
                saved["controls_sha256"] = digest(controls_path)
            elif scenario == "stale-workflow":
                (root / "gateway.jsonl").write_text("different observations")
            elif scenario == "stale-source":
                (Path(controls[1]["source"]) / "impl/module.py").write_text("changed source")
            elif scenario == "invalid-saved-verdict":
                saved["controls"][1]["result"]["grading"]["score"] = 0
            elif scenario == "malformed-control":
                del saved["controls"][1]["result"]["source_sha256"]
            elif scenario == "replay-error":
                source_failures.clear()
            elif scenario != "fresh-receipt":
                raise ValueError("unknown scenario")
            receipt_path = root / "quality.json"
            write(receipt_path, saved)
            try:
                accepted = rv.validate_receipt(receipt_path, task, image)
            except ValueError:
                return {"accepted": False}
            rows = accepted["controls"][0]["result"]["grading"]["checks"]
            return {
                "accepted": True,
                "fresh_observations": all(row.get("observation") == "fresh" for row in rows),
            }


requests = json.load(sys.stdin)["requests"]
print(json.dumps({"observations": [observe(request) for request in requests]}))
