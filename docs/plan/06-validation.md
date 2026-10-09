# Validation and evidence

## Decisions

Behavior and live telemetry are separate verification layers. Live proof is required before claiming completion. Evidence belongs in ignored .local/verification.

## Steps

Run make test/verify/verify-sentry; inspect browser; restart worker/agent/gateway; run negative network test; bootstrap and deploy repeatedly; inspect real Sentry IDs.

## Acceptance

All documented acceptance tests pass; evidence records exact run/trace/event IDs and timestamps; missing telemetry after 120 seconds fails explicitly.

## Completion checklist

- [ ] Implementation complete
- [ ] Automated checks complete
- [ ] Live verification recorded where applicable
- [ ] Documentation matches observed behavior

## Required assertions

Healthy: one order/stock decrement/outbox/notification, no pending message. Cache: miss+hit under prior activity. SlowDB: >=1.2s with explaining span. Go failure: stock unchanged. All origin errors: correct event ID/project/active span and readable source. Async: exactly3 attempts; recover noDLQ; exhaust oneDLQ+oneissue. Duplicate submit/publication/redelivery: no extra effects.

Restarts: worker resumes pending message; agent avoids replay; gateway recovers export without app failures. Negative network probe establishes isolation. Wrong-context reset aborts. Repeated apply/startup and clean data reset succeed.

## Live evidence

Trigger real app routes/buttons, not standalone capture scripts. Record exact run/trace/error/log IDs in ignored `.local/verification`. Verify through Sentry plugin or authenticated CLI/API; inspect trace parentage, log native context, original application pod UID, project mapping and source context. Poll up to120s; absent evidence is a failing check, not success.

## Commands and evidence files

1. `make test`: Python behavior, JavaScript contracts/outbox transport and Go unit tests. Native development testing additionally needs Go1.25/Python3.13/uv; Docker builds need only the baseline host tools.
2. `make verify`: process/cluster readiness, allowed and denied network paths, eight API scenarios, SQL counts, inventory deltas, cache behavior, Stream publication/ACK and retries.
3. `make source-map-upload`: extract assets from the current images, confirm debug IDs, upload matching web and worker maps. This token is separate from collector credentials.
4. `make verify-sentry`: bounded live trace/log/error assertions against the authenticated Sentry API. Inspect through the Sentry plugin as an independent gate.
5. Exercise all eight UI cards, including both retry modes. Require visible terminal states and valid diagnostic identifiers. Confirm the browser event shares the backend trace.
6. Rehearse interrupted pending work, agent checkpoints, persistent export queues, exporter switching, propagation patches, wrong-context refusal, reset, and repeated deployment.

Private evidence includes timestamps, revision/image identities, exact run/trace/event IDs and assertion results. Public verification reports summarize observed behavior with sanitized examples. A nonempty trace alone is insufficient: require expected project/service coverage, exact error-span linkage, native log context and originating pod metadata.
