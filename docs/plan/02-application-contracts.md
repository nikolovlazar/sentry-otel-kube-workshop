# Application contracts and data

## Decisions

Next web orchestrates Python HTTP and Go gRPC. Catalog and fulfillment own separate database schemas. Worker never accesses PostgreSQL. proto/workshop.proto is authoritative.

## Steps

Implement BeginRun/SubmitOrder/GetRun/ListRuns/RecordFailure/RecordNotificationResult; migrations and fixtures; outbox and idempotent queue; eight UI cards.

## Acceptance

Each run preserves immutable scenario/mode/trace ID; rollback restores inventory; repeated submissions and deliveries produce one terminal effect.

## Completion checklist

- [ ] Implementation complete
- [ ] Automated checks complete
- [ ] Live verification recorded where applicable
- [ ] Documentation matches observed behavior

## HTTP and gRPC

POST `/api/runs` accepts only an allowlisted scenario and optional worker mode; it generates UUID and starts a durable run. GET `/api/runs/:id` returns its state; GET `/api/runs` returns latest20 in active session; POST browser-error endpoint records browser SDK event. Public health routes are `/health/live` and `/health/ready`.

Python GET `/v1/products/demo-widget` accepts validated run UUID, scenario, cache policy (`default/fresh/bypass`) and session header. Response is `{productId,name,unitPriceCents,cached}`. Error response is `{errorCode,errorEventId,failureService}`. No arbitrary delay or SQL parameters.

`proto/workshop.proto` defines the six RPCs and camelCase client result mapping. Go errors include `sentry-event-id` and `workshop-error-code` trailing metadata. Run statuses: running, queued, retrying, completed, failed, dead_lettered. Notification outcomes: retry, succeeded, dead_lettered.

## State and reliability

Catalog owns products in its schema; fulfillment owns runs/inventory/orders/outbox. Worker and web never access the DB. Order/run IDs are equal; quantities are fixed at1. Initial stock10000. No cross-schema foreign keys.

SubmitOrder atomically commits inventory+order+outbox. Publisher resumes persisted W3C context, creates a producer span, and atomically deduplicates XADD by run ID. Stream `workshop:notifications`; group `notification-workers`; dedup `workshop:published`; attempts hash `workshop:attempts`; DLQ `workshop:dead-letters`; terminal DLQ index `workshop:dead-lettered`.

Worker ACK follows durable completion. Three attempts, delays250/500ms; XAUTOCLAIM resumes pending work. RecordNotificationResult is idempotent. Dedicated Redis allows reset of only `workshop:*`; session ID remains in records.

## Exact run and request contract

`POST /api/runs` accepts an allowlisted `scenario` and optional `workerMode`, plus the active session ID. The scenario strings are `full-journey`, `cache-miss-hit`, `slow-postgres`, `python-error`, `go-error`, `async-worker`, `next-error`, `browser-error`; worker modes are `success`, `recover`, `exhaust`. The async UI defaults to `exhaust`. Unknown values return400.

Accepted background work returns202. Origin Next.js failure returns500; propagated downstream failure returns502. These bodies retain the run ID and diagnostic identifiers. Poll `GET /api/runs/:id` for durable state; listing returns the latest20 runs in the active session. Browser errors record only a valid event ID through `POST /api/runs/:id/browser-error`.

Run JSON fields are `runId`, `sessionId`, `scenario`, `workerMode`, `status`, `traceId`, `attempts`, `failureService`, `errorCode`, `errorEventId`, `createdAt`, `updatedAt`. The authoritative protobuf uses corresponding snake-case field names and defines six RPCs. Go owns transitions from `running` to `queued`, `retrying`, and `completed`; `failed`/`dead_lettered` are terminal. Completion RPCs cannot alter an already terminal outcome.

HTTP/gRPC dependency deadlines are3s. The UI polls1s for15s, then offers refresh. Catalog query parameters are validated; cache keys include the run ID for isolated demonstrations. No UI input accepts raw SQL, delay duration or arbitrary failure injection.

## Persistence and idempotence

The SQL Job seeds `demo-widget` and10000 inventory units. Catalog owns `catalog.products`; fulfillment owns runs, inventory, orders, outbox. Roles own pre-created schemas and cannot create unrelated schemas. Only these two services have database URLs.

SubmitOrder locks the run and changes inventory/order/outbox in one transaction. Run IDs prevent duplicate orders. The Go publisher persists W3C carrier context and deduplicates Redis publication with Lua. The notification consumer uses one-message `XREADGROUP`, recovers pending entries with `XAUTOCLAIM`, persists attempts, and calls Go before acknowledging.

Retries wait250ms and500ms; recover mode succeeds on attempt3, exhaust mode emits one dead letter. An atomic Lua operation adds the terminal-error outbox with the dead letter. A separate drain retries SDK delivery using a stable event ID and original consumer context; business completion/ACK never depends on Sentry availability.

Run `make test` for contracts; `make verify` adds live DB/cache/Stream invariants. The Go integration harness starts disposable isolated PostgreSQL/Redis containers and verifies rollback, duplicate RPC/publication and immutable terminal states.
