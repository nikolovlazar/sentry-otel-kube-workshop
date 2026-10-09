# Deterministic scenarios

## Decisions

full-journey, cache-miss-hit, slow-postgres, python-error, go-error, async-worker (recover/exhaust), next-error, browser-error.

## Steps

Run each card; inspect trace, issue and logs; confirm SQL rollback, cache transitions and queue retries. Slow database uses SELECT pg_sleep(1.2), not application sleep.

## Acceptance

Nine permutations pass; origin exceptions captured once; exactly three async attempts; one dead letter on exhaustion; cache remains deterministic with prior activity.

## Completion checklist

- [ ] Implementation complete
- [ ] Automated checks complete
- [ ] Live verification recorded where applicable
- [ ] Documentation matches observed behavior

## Exact behavior

- full-journey: fixed product/quantity, completion through worker.
- cache-miss-hit: same run-specific key, first DB read/population then hit, never prewarmed by another run.
- slow-postgres: cache bypass and actual1.2s database query.
- python-error: uncached DB read then DemoCatalogError; no order/outbox.
- go-error: inventory update inside transaction then failure before commit; unchanged stock.
- async-worker recover: fail1,fail2,succeed3; no terminal issue/DLQ.
- async-worker exhaust: fail3 and one final SDK issue/DLQ.
- next-error: BeginRun then fail before catalog.
- browser-error: active browser action span, explicit exception capture, event ID persisted via BFF.

UI displays run/trace IDs,status,attempts,origin and event ID with Sentry navigation. One active run per browser; concurrent browsers remain independent. Status polling does not wait for telemetry ingestion.

## Nine acceptance permutations

| Scenario | Fault/behavior | Terminal state | Evidence |
| --- | --- | --- | --- |
| Full journey | All services, one queue delivery | completed, attempt1 | One order and stock decrement, four workloads in one trace |
| Cache miss/hit | Fresh run-specific key, two lookups | completed | One miss plus one hit; one SQL product read |
| Slow PostgreSQL | Cache bypass, `pg_sleep(1.2)` | completed | DB span at least1.2s |
| Python error | Raise after product read | failed | Catalog event linked to `catalog.lookup`; no order |
| Go error | Fail after inventory UPDATE before commit | failed | Fulfillment event and rollback; stock unchanged |
| Async recover | Fail attempts1/2, succeed3 | completed | Three sibling consumer spans, no final issue/DLQ |
| Async exhaust | Fail all three attempts | dead_lettered | One stable Node event, one DLQ record, original consumer span |
| Next.js error | Fail after BeginRun, before catalog | failed | Web server event, no catalog/order |
| Browser error | Catch deliberate exception inside action span | failed | Browser event; same trace as BeginRun |

Run buttons are serialized in the UI. Every result exposes run/trace/event identifiers and Sentry links. Upstream services log/mark the failure without another issue capture. Slow/cache scenarios always work independently of previous activity.

Prerequisite: `make up` plus real Sentry configuration. Run `make verify` for eight HTTP permutations and browser checks for the ninth. After resets, start new marked runs; retained Sentry history does not count as evidence for a newly deployed image.
