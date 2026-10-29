"""Authored VM-only observations; expected values stay on the host."""

import json
import sys

sys.path.insert(0, sys.argv[1])
import admission

observations = []
for request in json.load(sys.stdin)["requests"]:
    if request["operation"] == "admit":
        observations.append(admission.admit(request["saved"], request["current"]))
    else:
        observations.append(admission.format_status(request["value"]))
print(json.dumps({"observations": observations}))
