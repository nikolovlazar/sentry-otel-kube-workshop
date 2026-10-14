# Application contracts and data

## Decisions

Next web orchestrates Python HTTP and Go gRPC. Catalog and fulfillment own separate database schemas. Worker never accesses PostgreSQL. proto/workshop.proto is authoritative.

## Steps

Implement BeginRun/SubmitOrder/GetRun/ListRuns/RecordFailure/RecordNotificationResult; migrations and fixtures; outbox and idempotent queue; five primary UI cards and four additional examples.

## Acceptance

Each run preserves immutable scenario/mode/trace ID; rollback restores inventory; repeated submissions and deliveries produce one terminal effect.

## Completion checklist

- [x] Implementation complete
- [x] Automated checks complete
- [x] Live verification recorded where applicable
- [x] Documentation matches observed behavior

## Prerequisites, commands and expected results

Use the [developer prerequisites](../../README.md#developer-tests) for `make test`; use a configured `make up` baseline for live checks. Run `make test`, then `make verify`. Expect validated contracts, separate schema ownership, inventory rollback, one order/publication per run and correct retry/DLQ states. The disposable Go harness command is `cd services/fulfillment && python3 tools/run_integration.py`; it must pass without changing workshop fixtures.

## HTTP and gRPC

POST `/api/runs` accepts only an allowlisted scenario and optional worker mode; it generates UUID and starts a durable run. GET `/api/runs/:id` returns its state; GET `/api/runs` returns latest20 in active session; POST browser-error endpoint records browser SDK event. Public health routes are `/health/live` and `/health/ready`.

Python GET `/v1/products/demo-widget` accepts validated run UUID, scenario, cache policy (`default/fresh/bypass`) and session header. Response is `{productId,name,unitPriceCents,cached}`. Error response is `{errorCode,errorEventId,failureService}`. No arbitrary delay or SQL parameters.

`proto/workshop.proto` defines the six RPCs and camelCase client result mapping. Go errors include `sentry-event-id` and `workshop-error-code` trailing metadata. Run statuses: running, queued, retrying, completed, failed, dead_lettered. Notification outcomes: retry, succeeded, dead_lettered.

## State and reliability

Catalog owns products in its schema; fulfillment owns runs/inventory/orders/outbox. Worker and web never access the DB. Order/run IDs are equal; quantities are fixed at1. Initial stock10000. No cross-schema foreign keys.

SubmitOrder atomically commits inventory+order+outbox. Publisher resumes persisted W3C context, creates a producer span, and atomically deduplicates XADD by run ID. Stream `workshop:notifications`; group `notification-workers`; dedup `workshop:published`; attempts hash `workshop:attempts`; DLQ `workshop:dead-letters`; terminal DLQ index `workshop:dead-lettered`; durable SDK-error hash `workshop:error-outbox`.

Worker ACK follows durable completion. The normal retry budget is three attempts, with delays250/500ms; a crash can replay an attempt while preserving the same budget and idempotent effects. XAUTOCLAIM resumes pending work. RecordNotificationResult is idempotent. Dedicated Redis allows reset of only `workshop:*`; session ID remains in records.

## Exact run and request contract

`POST /api/runs` accepts an allowlisted `scenario` and optional `workerMode`, plus the active session ID. The scenario strings are `full-journey`, `cache-miss-hit`, `slow-postgres`, `postgres-lock-timeout`, `python-error`, `go-error`, `async-worker`, `next-error`, `browser-error`; worker modes are `success`, `recover`, `exhaust`. The async UI defaults to `exhaust`. Unknown values return400.

Accepted background work returns202. Origin Next.js failure returns500; propagated downstream failure returns502. These bodies retain the run ID and diagnostic identifiers. Poll `GET /api/runs/:id` for durable state; listing returns the latest20 runs in the active session. Browser exceptions record only a valid event ID through `POST /api/runs/:id/browser-error`.

Run JSON fields are `runId`, `sessionId`, `scenario`, `workerMode`, `status`, `traceId`, `attempts`, `failureService`, `errorCode`, `errorEventId`, `createdAt`, `updatedAt`. The authoritative protobuf uses corresponding snake-case field names and defines six RPCs. Go owns transitions from `running` to `queued`, `retrying`, and `completed`; `failed`/`dead_lettered` are terminal. Completion RPCs cannot alter an already terminal outcome.

HTTP/gRPC dependency deadlines are3s. The UI polls1s for15s, then offers refresh. Catalog query parameters are validated; the cache miss/hit scenario uses a run-specific key for an isolated demonstration, while ordinary lookups can use the shared key. No UI input accepts raw SQL, delay duration or arbitrary failure injection.

## Persistence and idempotence

The SQL Job seeds `demo-widget` and10000 inventory units. Catalog owns `catalog.products`; fulfillment owns runs, inventory, orders, outbox. Roles own pre-created schemas and cannot create unrelated schemas. Only these two services have database URLs.

SubmitOrder locks the run and changes inventory/order/outbox in one transaction. Run IDs prevent duplicate orders. The Go publisher persists W3C carrier context and deduplicates Redis publication with Lua. The notification consumer uses one-message `XREADGROUP`, recovers pending entries with `XAUTOCLAIM`, persists attempts, and calls Go before acknowledging.

Retries wait250ms and500ms; recover mode succeeds on attempt3, exhaust mode emits one dead letter. An atomic Lua operation adds the terminal-error outbox with the dead letter. A separate drain retries SDK delivery using a stable event ID and original consumer context; business completion/ACK never depends on Sentry availability.

Run `make test` for contracts; `make verify` adds live DB/cache/Stream invariants. The Go integration harness starts disposable isolated PostgreSQL/Redis containers and verifies rollback, duplicate RPC/publication and immutable terminal states.

## Bounded PostgreSQL contention

`postgres-lock-timeout` is an additional allowlisted scenario in web, catalog, and fulfillment validation. Public run/error response fields and the protobuf contract are unchanged. It bypasses Redis. Within the active catalog business span, one PostgreSQL connection holds an exclusive lock on `catalog.products`; a second performs the ordinary product read with a 750 ms session-local lock timeout.

Capture the resulting PostgreSQL exception once in catalog and return `failureService: catalog`, `errorCode: POSTGRES_LOCK_TIMEOUT`, and its event ID. The run becomes `failed` before order submission. Release the locker in `finally`; a two-second idle transaction timeout bounds a crashed connection's lock. Never expose raw SQL or arbitrary fault durations in the UI. PostgreSQL connection `application_name` carries `w:<trace-id>:<span-id>` from `catalog.product_query`, the business span enclosing those SQL statements, for native-log correlation. The SDK error is captured by the parent `catalog.lookup` span in the same trace.

Verify no order/outbox/inventory effect, one originating error, the failed SQL span, native lock/error records, and a succeeding healthy request immediately afterward. The four synthetic language/browser errors retain their original contracts in More examples.
