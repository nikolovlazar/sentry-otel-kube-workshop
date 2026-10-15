# Deterministic scenarios

## Decisions

Five primary cards follow the presentation order: Complete journey, Background job, Slow database, Database contention, and Cache miss → hit. Find affected requests and aggregate analysis follow in Sentry. The four synthetic Python, Go, Next.js, and browser exceptions remain available in collapsed More examples. Background job retains success, recover, and exhaust controls; exhaust remains its default.

## Ordered steps and prerequisites

1. Start `make up` with current Sentry configuration and processed JavaScript source maps.
2. Run the five primary cards, using both recover and exhaust background modes.
3. Expand More examples and run all four preserved origin-error cards.
4. Run `make verify` for API/database/queue invariants, `make verify-sentry` for application evidence, and `make verify-infra` for native infrastructure evidence.
5. Inspect current run/trace/event IDs, resource metadata, and native log context; do not reuse evidence from an earlier image.

Expected results are durable terminal states, deterministic cache/latency behavior, one origin exception capture, and correct database/queue effects. The UI polls application state independently of telemetry indexing.

## Exact behavior and acceptance

| Scenario | Fault/behavior | Terminal state | Evidence |
| --- | --- | --- | --- |
| `full-journey` | Fixed product and quantity, one queued delivery | completed, attempt 1 | One order/stock decrement; all application services in one trace |
| `cache-miss-hit` | Fresh run-specific cache key, two lookups | completed | Redis miss/populate/hit, one SQL product read |
| `slow-postgres` | Cache bypass and actual `pg_sleep(1.2)` | completed | SQL span at least 1.2 s; retained native PostgreSQL duration log |
| `postgres-lock-timeout` | Exclusive product-table lock, second-connection read with 750 ms lock timeout | failed | `POSTGRES_LOCK_TIMEOUT`, catalog issue/failed SQL span, native lock/error logs, no order or stock change |
| `async-worker` recover | Fail attempts 1/2, succeed on 3 | completed | Three sibling consumer spans; no terminal issue/DLQ |
| `async-worker` exhaust | Fail all three attempts | dead_lettered | One stable Node event, one DLQ record, original consumer context |
| `python-error` | Uncached product read then `DemoCatalogError` | failed | Catalog event linked to `catalog.lookup`; no order |
| `go-error` | Inventory UPDATE then failure before commit | failed | Fulfillment event, transaction rollback, unchanged stock |
| `next-error` | BeginRun then fail before catalog | failed | Web server event; no catalog/order |
| `browser-error` | Caught deliberate exception in active browser action span | failed | Browser event; same trace as backend BeginRun |

These are ten acceptance permutations: nine API cases plus the real browser case. Async first-attempt success is covered by Complete journey and remains available in the selector.

Contention bypasses Redis. Release the exclusive lock in `finally`, with a two-second idle transaction timeout protecting a crashed locker. A healthy journey immediately afterward proves recovery without storage restart. Native PostgreSQL records use the enclosing business-span context supplied through `application_name`; service/pod identity remains PostgreSQL.

## Presentation and exercises

The 35-minute demo follows the five primary cards. Show slow query → timeout → error/trace/native logs as a single investigation. Keep additional synthetic failures, pod replacement, and Redis busy faults outside the timed path.

The finite `make demo-infra-sampling` exercise compares 2,000 routine PostgreSQL messages at 100% and 5% retention, then restores the prior gateway rate. `make demo-redis-busy` emits a genuine Redis slow-script warning using a fixed read-only script lasting approximately 400 ms with a 100 ms threshold. The warning survives sampling; no Stream data is modified. Both generators terminate.

## Completion checklist

- [x] Original cache, latency, origin-error, and queue scenarios implemented
- [x] Five-card view and collapsed original examples implemented
- [x] New database contention and infrastructure exercises verified against deployed images
- [x] Fresh native Sentry evidence, nine backend permutations, and real browser error recorded
- [x] All nine UI cards plus recover mode verified with ten distinct run/trace IDs
- [x] Fresh idle health-check observation and normal positive control passed
- [ ] Human 35-minute narration rehearsal completed

Live revision results are recorded in [verification](../verification.md). Checked historical baseline acceptance does not substitute for new image/configuration evidence.
