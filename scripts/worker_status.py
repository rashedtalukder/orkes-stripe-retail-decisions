"""Show which workers are polling each task queue (server-side view) and queue depth."""
from stripe_decisions.conductor_api import ConductorREST

api = ConductorREST()
for t in ["sd_fetch_billing_facts", "sd_classify_intent", "sd_execute_refund"]:
    polls = api._raw("GET", f"/tasks/queue/polldata?taskType={t}") or []
    size = api._raw("GET", f"/tasks/queue/sizes?taskType={t}")
    print(t, "queue:", size, "pollers:", [(p.get("workerId"), p.get("domain"), p.get("lastPollTime")) for p in polls])
