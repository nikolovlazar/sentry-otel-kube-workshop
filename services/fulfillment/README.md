# Fulfillment service

Go owns `fulfillment.runs`, `inventory`, `orders`, and `outbox`; Python owns catalog products. The application database role must exist before startup. Embedded `migrations.sql` is additive and does not overwrite inventory on restart.

Run `go test ./...`, `go vet ./...`, and `go build .`. Integration tests additionally use `TEST_DATABASE_URL` and `TEST_REDIS_URL`, dedicated throwaway PostgreSQL/Redis instances. They truncate only the fulfillment schema in that test database and delete queue keys only in that dedicated test Redis; never point them at the workshop or another database.

To regenerate committed protobuf files, run `cd tools && go run ./generate`. The generator compiles the authoritative `../../../proto/workshop.proto` with pinned Go tools; module checksums verify downloads. No system protoc is needed.

Required configuration: `DATABASE_URL`, `REDIS_URL`, `WORKSHOP_SESSION_ID`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `SENTRY_DSN`. Optional resource configuration: `OTEL_SERVICE_NAME` (default `fulfillment`), `SENTRY_RELEASE`, `POD_UID`, `POD_NAME`, `POD_NAMESPACE`, `NODE_NAME`. OTLP endpoint is the Collector HTTP base on port 4318; errors go through the Sentry SDK. JSON stdout is the only log path.

Ports: gRPC 50051, HTTP health/readiness 8081 (`/healthz`, `/readyz`). Every RPC has at most a three-second deadline. Readiness checks PostgreSQL and Redis; Redis outages retain work in the transactional outbox.

`SubmitOrder` commits inventory, order, and an outbox row together. Its injected Go failure rolls back to the reservation savepoint while keeping the run lock, then stores one linked SDK event and failed state. Trailers: `sentry-event-id`, `workshop-error-code`.

The outbox publisher resumes the stored W3C carrier and creates a PRODUCER span. A Redis Lua script atomically deduplicates publication by run ID (`workshop:published`) into `workshop:notifications`. The consumer must use the published carrier as its parent. Go accepts notification outcomes `retry`, `succeeded`, and `dead_lettered`; retries are capped at three and terminal state never changes on redelivery.
