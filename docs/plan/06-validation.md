# Validation and evidence

## Decisions

Behavior and live telemetry are separate verification layers. Live proof is required before claiming completion. Evidence belongs in ignored .local/verification.

## Steps

Run make test/build/deploy/verify-collector/verify/verify-recovery/verify-sentry/verify-infra; use source-map-upload only for guarded recovery; inspect browser; restart worker/agent/gateway; run negative network test; bootstrap and deploy repeatedly; inspect real Sentry IDs.

## Acceptance

All documented acceptance tests pass; evidence records exact run/trace/event IDs and timestamps; missing telemetry after the configured visibility window fails explicitly (120 seconds by default).

## Completion checklist

- [x] Implementation complete
- [x] Automated checks complete
- [x] Live verification recorded where applicable
- [x] Documentation matches observed behavior

## Prerequisites

Use a successful `make up` baseline for live checks, a verification token with Events Read, and a real browser for browser SDK evidence. Source and isolated checks use the [developer prerequisites](../../README.md#developer-tests). The commands below fail on missing evidence; compare their results with every required assertion before marking completion.

## Required assertions

Healthy: one order/stock decrement/outbox/notification, no pending message. Cache: miss+hit under prior activity. SlowDB: >=1.2s with explaining span. Go failure: stock unchanged. All origin errors: correct event ID/project/active span and readable source. Async: exactly3 attempts in an uninterrupted run; crash redelivery can replay an attempt without extra terminal effects; recover noDLQ; exhaust oneDLQ+oneissue. Duplicate submit/publication/redelivery: no extra effects.

Restarts: worker resumes pending message; agent avoids replay. Gateway-down recovery must prove the agent's retry to the gateway while application state completes. Separately, in static OTLP/HTTP mode, block the remote endpoint, restart the gateway with its persistent queue, restore access within the retry window and require the marked trace/log evidence. Restore main mode afterward. Main Sentry Exporter persistence does not prove retry after a failed remote HTTP batch. Negative network probe establishes isolation. Wrong-context reset aborts. Repeated apply/startup and clean data reset succeed.

## Live evidence

Trigger real app routes/buttons, not standalone capture scripts. Record exact run/trace/error/log IDs in ignored `.local/verification`. Verify through Sentry plugin or authenticated CLI/API; inspect trace parentage, log native context, original application pod UID, project mapping and source context. Poll for `WORKSHOP_VERIFICATION_TIMEOUT_SECONDS` (default 120, allowed integers 30–600); absent evidence is a failing check, not success. An explicit longer rehearsal window is recorded in evidence and does not convert the strict 120-second failure into a pass.

## Commands and evidence files

1. `make test`: Python behavior, JavaScript contracts/outbox transport and Go unit tests. Native development testing additionally needs Go1.25/Python3.13/uv; Docker builds need only the baseline host tools.
2. `make verify`: process/cluster readiness, allowed and denied network paths, nine API scenarios, SQL counts, inventory deltas, cache behavior, Stream publication/ACK and retries.
3. `make build` (also part of `make up`): both JavaScript npm builds compile/inject debug IDs, upload matching maps and wait for processing before deployment. Missing required credentials or upload/processing failure fails the workshop build. The upload token is separate from collector credentials. Optional `make source-map-upload` recovery extracts assets only after verifying all running web/worker images and release/version values.
4. `make verify-sentry`: bounded live trace/log/error assertions against the authenticated Sentry API. Inspect through the Sentry plugin as an independent gate.
5. Run `make verify-recovery` for the isolated process-crash/SDK-outage gate, then exercise all nine UI cards, including both retry modes. Require visible terminal states and valid diagnostic identifiers. Confirm the browser event shares the backend trace.
6. Rehearse interrupted pending work, agent checkpoints, agent-to-gateway retry and static OTLP/HTTP persistent-queue/remote retry, exporter switching, propagation patches, wrong-context refusal, reset, and repeated deployment.

Record exporter mode, blocked hop, outage/restart timing and marked run IDs for collector recovery evidence. A main-mode gateway-down check is not evidence of Sentry HTTP outage recovery. See [retry and persistence limits](04-collector-and-sentry.md#retry-and-persistence-limits).

Private evidence includes timestamps, revision/image identities, exact run/trace/event IDs and assertion results. Public verification reports summarize observed behavior with sanitized examples. A nonempty trace alone is insufficient: require expected project/service coverage, exact error-span linkage, native log context and originating pod metadata.

## Recorded source and isolated-test checks — 2026-10-09

The following checks ran before the final deployed-image/Sentry acceptance gate. The service test suite and automatic source-map host builds were repeated at revision `bc7d9c22`. These results establish source, host-build and isolated behavior; they do not mark the live-completion checklist above.

| Command | Observed result |
| --- | --- |
| `make test` at `bc7d9c22` | Locked `npm ci` in both JS packages and frozen uv setup succeeded; catalog 4 tests, web 3 tests, worker 5 tests and Go unit tests passed |
| `make verify-recovery` | Real worker processes and pinned disposable Redis passed crash/reclaim, one DLQ, fixed stored retry budget, terminal/ACK during SDK HTTP500, later HTTP200 outbox drain, stable event ID and exact original consumer trace/span; no test containers/processes remained |
| `cd services/fulfillment && go vet ./...` | Passed |
| `cd services/fulfillment && python3 tools/run_integration.py` | Disposable PostgreSQL/Redis race tests passed: transaction rollback, one origin SDK capture/trailers, duplicate order/publication, persisted producer parent and workflow baggage, immutable terminal state, restricted-role migration repeatability |
| `npm --prefix apps/web audit --json` and `npm --prefix services/notification-worker audit --json` with `.local/bin` on PATH | Both reported zero vulnerabilities after the gRPC client patch |
| Recovery harness interrupted with SIGINT after an actual worker process spawned | Exit130; detached worker process group and its disposable Redis container were both removed |
| `npm --prefix apps/web run build` and `npm --prefix services/notification-worker run build` with required upload configured | Both real host builds uploaded matching maps and reported completed Sentry processing |
| Both JavaScript npm builds with explicit no-token/skip configuration | Compilation passed and each build reported that source-map upload was skipped |
| Build-wrapper environment/privacy checks | 8 checks passed for explicit environment precedence, root `.env` allowlisting and credential isolation |
| Private compiled-artifact token scan | 2,237 generated artifact files inspected; zero upload-token matches |
| `git diff --check` | Passed |

For Go's destructive fixture tests, use only the disposable harness or dedicated throwaway `TEST_DATABASE_URL`/`TEST_REDIS_URL` instances. The recovery check uses local fake gRPC/Sentry receivers; the live gate remains `make verify-sentry` plus real browser inspection. Rebuild/redeploy and repeat that gate after runtime source changes.

## Recorded collector rehearsals — 2026-10-09

| Mode and check | Observed result | Private evidence |
| --- | --- | --- |
| Main Sentry Exporter: gateway-down recovery and agent checkpoint restart | Passed; two fresh Complete journey traces recovered. This proves the agent-to-gateway hop, not retries of failed Sentry HTTP batches | `.local/verification/rehearsal-collectors.json` |
| Static OTLP/HTTP: blocked gateway HTTPS and persistent-queue restart | Passed; observed queue size 5 and retry activity, retained the same gateway PVC across restart, then restored egress. Five marked traces each contained 47 spans and 16 logs; every original stdout record ID/native identity recovered with native span linkage and complete parentage | `.local/verification/rehearsal-static-durable.json` |

The static rehearsal used `python3 .local/verification/static-durable-rehearsal.py`, a private rehearsal script. Main exporter mode was restored afterward. These observations exercise the configured static 120-second retry window; they do not extend main-mode guarantees. Final application/browser/release acceptance is recorded separately before marking the completion checklist.

## Native infrastructure revision acceptance

1. Run `make verify-collector` against the pinned Collector image. Validate both export modes, native PostgreSQL/Redis parsing, timestamps/severities, actual service/pod metadata, valid PostgreSQL context, rejection of invalid/all-zero context, and absent fabricated Redis context. Require marked application, warning/error, and recognized slow/lock records to survive sampling.
2. Run `make verify` for nine backend permutations and a real browser check for the tenth. Contentious reads must fail with `POSTGRES_LOCK_TIMEOUT`, one originating Python issue, a failed database span, no order/inventory change, and a healthy request immediately afterward.
3. Run `make verify-sentry` and `make verify-infra`. Native PostgreSQL slow/lock/error records must retain infrastructure routing and genuine pod identity, with native context matching the supplied `catalog.product_query` business span. The originating SDK error links to its active parent `catalog.lookup` span in that same trace; Redis native OTLP context is empty and investigated by pod/time. Sentry may display a standalone ingestion trace ID; absent `span_id` and the fixture output establish that it is not linked application context.
4. Run `make demo-infra-sampling`. Separate marked 2,000-record routine PostgreSQL batches compare 100% versus 5%. Record generated/exported counts and the statistical retention assertion in `.local/verification/infra-sampling.json`. Verify original configured rate restored even on failure; root `.env` is unchanged.
5. Run `make demo-redis-busy`. Require the real warning despite routine sampling, original Redis pod/no restart, preserved Stream data, successful PING and healthy workflow, and no generator left running. Store `.local/verification/redis-busy.json`.
6. Repeat collector fixture checks and marked application/incident preservation in both mutually exclusive export modes. Ensure queue parentage, duplicate delivery, health exclusions, and existing scenarios still pass. `make verify-infra` stores combined live evidence in `.local/verification/infra-live.json`.
7. Rehearse the [five-card flow](../workshop/presenter-guide.md) within 35 minutes, after provisioning/build/reset. Both finite generators run outside the timed demonstration.

New live verification is independent of the recorded 2026-10-09 baseline. Keep application traces/logs at 100%, routine PostgreSQL/Redis logs at configured 5/10/100, and infrastructure warnings/errors plus recognized PostgreSQL slow-query/lock-wait records at 100%. Other components retain existing behavior. No trace tail sampler is part of this revision.

Current revision browser acceptance passed all nine cards plus recover mode with ten distinct run/trace IDs. The five primary clicks completed in 30 seconds, excluding narration and remote visibility. The fresh health gate recorded twelve direct 200 responses plus native probes and zero accepted/exported spans in a 30-second idle interval, with a successful normal positive control. See the private browser/health evidence and the [sanitized current report](../verification.md). Human 35-minute spoken rehearsal remains separate.
