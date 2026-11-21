"""Print why a workflow failed: workflow reason + each task's status/reason."""
import sys
from stripe_decisions.conductor_api import ConductorREST

api = ConductorREST()
for wid in sys.argv[1:]:
    wf = api.get_execution(wid)
    print(wid, wf["status"], wf.get("reasonForIncompletion"))
    for t in wf.get("tasks", []):
        print("  ", t["referenceTaskName"], t["status"], (t.get("reasonForIncompletion") or "")[:300],
              "retry", t.get("retryCount"), "worker", t.get("workerId"))
