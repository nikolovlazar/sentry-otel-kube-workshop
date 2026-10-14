#!/usr/bin/env python3
"""Exercise real BFF workflows and inspect durable DB/queue invariants."""
from datetime import datetime, timezone
import json
import re
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import UUID
import workshop as w


def http(path, payload=None):
    request = Request("http://localhost:8080" + path, data=json.dumps(payload).encode() if payload is not None else None, headers={"Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=15) as response: return json.load(response)
    except HTTPError as response:
        value = json.load(response)
        if "runId" not in value: raise RuntimeError("Workflow API error: " + str(value))
        return value


def sql(query):
    result = w.kc("-n", w.APP, "exec", "postgres-0", "--", "psql", "-U", "workshop", "-d", "workshop", "-At", "-v", "ON_ERROR_STOP=1", "-c", query, capture=True)
    return result.stdout.strip()


def redis(*args):
    result = w.kc("-n", w.APP, "exec", "redis-0", "--", "sh", "-c", 'export REDISCLI_AUTH="$REDIS_PASSWORD"; exec redis-cli --json "$@"', "redis-cli", *args, capture=True)
    return json.loads(result.stdout)


def terminal(run):
    deadline = time.monotonic() + 20
    while run["status"] not in ["completed", "failed", "dead_lettered"]:
        if time.monotonic() > deadline: raise AssertionError("Run did not terminate: " + run["runId"])
        time.sleep(.3)
        run = http("/api/runs/" + run["runId"])
    return run


def main():
    w.forward()
    evidence = {"timestamp": datetime.now(timezone.utc).isoformat(), "runs": []}
    for scenario, mode, expected, submitted in [
        ("full-journey", "success", "completed", True),
        ("cache-miss-hit", "success", "completed", True),
        ("slow-postgres", "success", "completed", True),
        ("postgres-lock-timeout", "success", "failed", False),
        ("python-error", "success", "failed", False),
        ("go-error", "success", "failed", False),
        ("next-error", "success", "failed", False),
        ("async-worker", "recover", "completed", True),
        ("async-worker", "exhaust", "dead_lettered", True),
    ]:
        before = int(sql("SELECT available_quantity FROM fulfillment.inventory WHERE product_id='demo-widget'"))
        started = time.monotonic()
        run = terminal(http("/api/runs", {"scenario": scenario, "workerMode": mode}))
        duration = time.monotonic() - started
        UUID(run["runId"])
        assert run["status"] == expected, (scenario, run)
        assert re.fullmatch("[a-f0-9]{32}", run["traceId"]) and run["traceId"] != "0" * 32
        count = int(sql("SELECT count(*) FROM fulfillment.orders WHERE run_id='" + run["runId"] + "'"))
        assert count == int(submitted), (scenario, "order count", count)
        after = int(sql("SELECT available_quantity FROM fulfillment.inventory WHERE product_id='demo-widget'"))
        assert before - after == int(submitted), (scenario, "inventory changed unexpectedly")
        if expected in ["failed", "dead_lettered"]:
            assert re.fullmatch("[a-f0-9]{32}", run["errorEventId"]), (scenario, "missing SDK event ID")
        if scenario == "slow-postgres": assert duration >= 1.2
        if scenario == "postgres-lock-timeout":
            assert duration >= .75
            assert run["failureService"] == "catalog" and run["errorCode"] == "POSTGRES_LOCK_TIMEOUT"
            assert sql("SELECT count(*) FROM pg_locks WHERE relation='catalog.products'::regclass AND mode='AccessExclusiveLock'") == "0"
        if scenario == "async-worker": assert run["attempts"] == 3
        if submitted:
            outbox = sql("SELECT (published_at IS NOT NULL)::text FROM fulfillment.outbox WHERE run_id='" + run["runId"] + "'")
            assert outbox == "true", (scenario, "outbox not published")
            message = redis("HGET", "workshop:published", run["runId"])
            assert message, (scenario, "no publication index")
            pending = redis("XPENDING", "workshop:notifications", "notification-workers", message, message, "1")
            assert not pending, (scenario, "message remains pending")
        if scenario == "cache-miss-hit":
            records = w.kc("-n", w.APP, "logs", "deployment/catalog", "--since=10m", capture=True).stdout.splitlines()
            bodies = []
            for line in records:
                try:
                    record = json.loads(line)
                    if record.get("workshop.run_id") == run["runId"]: bodies.append(record.get("body"))
                except json.JSONDecodeError: pass
            assert bodies.count("Catalog cache miss") == 1 and bodies.count("Catalog cache hit") == 1, bodies
        if expected == "dead_lettered":
            messages = redis("XRANGE", "workshop:dead-letters", "-", "+")
            matches = [entry for entry in messages if run["runId"] in str(entry)]
            assert len(matches) == 1, "Expected one dead-letter message"
        evidence["runs"].append({**run, "durationSeconds": round(duration, 3), "orderCount": count})
        w.private_write(w.LOCAL / "verification/workflows.json", json.dumps(evidence, indent=2))
        print(scenario + "/" + mode + ": " + expected + "; database/queue invariants passed", flush=True)
    print("Nine API permutations passed. Real browser exception/correlation is checked separately through the UI.")

if __name__ == "__main__": main()
