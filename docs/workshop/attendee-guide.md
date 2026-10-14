# Attendee guide

Start from the working baseline. The exercises inspect and alter configuration; they do not require writing a storefront or implementing a new service.

## Lab 1 — bring up the cluster

Prerequisites: Docker-compatible runtime, own Sentry organization/team, five project DSNs and a source-map upload token. Fill ignored `.env`; choose a read-capable collector token or static exporter mode. Set `OTEL_INFRA_LOG_SAMPLE_PERCENT=5`, then run `make bootstrap`, `make doctor`, `make up`, `make verify-collector`, and `make verify` in that order. The full workshop build requires `SENTRY_SOURCE_MAP_AUTH_TOKEN`; both JavaScript builds automatically upload their matching maps and wait for processing before deployment. Failed upload/processing stops the build. No separate upload command is needed before the JavaScript error exercises. Automated Sentry verification also needs Events Read; see the [credential table](../plan/04-collector-and-sentry.md#credential-scope-by-purpose).

Checkpoint: localhost control centre loads, all workloads are ready, Complete journey reaches `completed`. Inspect the named context using `.local/bin/kubectl --kubeconfig .local/kubeconfig --context kind-sentry-otel-workshop get pods -A`.

## Lab 2 — follow collection

Read `infra/collectors/agent.json`, `gateway-sentry.json`, and the collector manifests. Identify the log mount, native trace fields, original-pod association, memory limiter, batching and service-to-project mapping.

Run one workflow and find its exact run ID in Sentry Logs. Compare its log trace ID with the UI. Inspect application pod UID/name and confirm they do not identify the collector.

Checkpoint: a log opens in the same trace as the application span.

Compare exporter retry settings. The agent retries its gateway hop; static OTLP/HTTP explicitly retries transient remote failures. The pinned main Sentry Exporter persists unsent batches but does not retry failed Sentry HTTP deliveries. See [recovery limits](../plan/04-collector-and-sentry.md#retry-and-persistence-limits).

## Lab 3 — switch export routes

Change only ignored `.env`: `SENTRY_EXPORTER_MODE=otlphttp`. Run `make deploy`; trigger a fresh workflow and verify its telemetry. Restore `sentry` mode and redeploy if you have the token.

Checkpoint: project destination and application behavior remain the same. There is one active exporter route, and records are not doubled.

## Lab 4 — PostgreSQL evidence plus error context

Run Slow database. Find `pg_sleep(1.2)` in the catalog trace and its native PostgreSQL duration log in the infrastructure project. Inspect PostgreSQL service and pod identity. Catalog supplies its enclosing `catalog.product_query` business-span context through `application_name`; the agent validates and assigns those trace/span IDs to native OTLP context fields.

Run Database contention. Inspect the real 750 ms SQL lock timeout, originating Python issue, and native PostgreSQL lock-wait/error logs. It ends `failed`, with `POSTGRES_LOCK_TIMEOUT`, no order, and unchanged inventory. Run Complete journey immediately afterward to confirm lock release. The two-second idle transaction timeout protects against an interrupted locker.

Expand More examples for the preserved Python and Go synthetic exceptions. Inspect their source locations and origin-only capture; Go failure rolls inventory back.

Checkpoint: native PostgreSQL evidence connects to its supplied business span, while one originating SDK event identifies the actual exception. `make verify-infra` checks the new native-log path separately from `make verify-sentry`. Native service filters use `resource.service.name:postgres` or `resource.service.name:redis`; application records also have a `service.name` attribute.

## Lab 5 — deliberately break propagation

Apply the supplied patch from the repository root:

```sh
.local/bin/kubectl --kubeconfig .local/kubeconfig --context kind-sentry-otel-workshop -n sentry-otel-workshop patch deployment web --type=strategic --patch-file infra/exercises/propagation/break.json
.local/bin/kubectl --kubeconfig .local/kubeconfig --context kind-sentry-otel-workshop -n sentry-otel-workshop rollout status deployment/web
```

Trigger Complete journey. The catalog's HTTP request becomes a separate trace because its carrier is intentionally omitted. Compare the web and catalog spans by run ID in Sentry. Restore the carrier:

```sh
.local/bin/kubectl --kubeconfig .local/kubeconfig --context kind-sentry-otel-workshop -n sentry-otel-workshop patch deployment web --type=strategic --patch-file infra/exercises/propagation/restore.json
.local/bin/kubectl --kubeconfig .local/kubeconfig --context kind-sentry-otel-workshop -n sentry-otel-workshop rollout status deployment/web
```

Trigger another journey. This configuration flag is internal to the exercise; it is never a control-centre input. `make verify-sentry` must fail its service coverage check while propagation is broken.
Checkpoint: broken carrier produces a split; restoring propagation reconnects the trace. Do not change the Sentry project mapping or SDK initialization for this exercise.

## Lab 6 — async and pod identity

In Background job, compare Recover on attempt 3 and Exhaust → dead letter modes. Inspect attempts and consumer parentage. Inspect the most recent Stream message:

```sh
.local/bin/kubectl --kubeconfig .local/kubeconfig --context kind-sentry-otel-workshop -n sentry-otel-workshop exec redis-0 -- sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli XREVRANGE workshop:notifications + - COUNT 1'
```

Find `traceparent`, `tracestate`, and `baggage`. These fields already travel through the SQL outbox and Redis message; the worker extracts them before creating sibling attempt spans. No new Redis context store is required.

Run the deterministic crash check from the repository root after installing the [developer test prerequisites](../../README.md#developer-tests):

```sh
make verify-recovery
```

This check uses its own Redis container, actual killed/restarted worker processes, and local fake gRPC/Sentry endpoints. It holds the terminal RPC after the Redis error handoff, kills the worker, and checks pending-message recovery. It also verifies that SDK downtime does not prevent terminal state or acknowledgement, and that later error delivery preserves the original consumer span.

To replace the worker in the workshop cluster, use the scoped commands below. A graceful rollout may finish an active attempt; the isolated check above controls the exact crash window.

```sh
.local/bin/kubectl --kubeconfig .local/kubeconfig --context kind-sentry-otel-workshop -n sentry-otel-workshop rollout restart deployment/notification-worker
.local/bin/kubectl --kubeconfig .local/kubeconfig --context kind-sentry-otel-workshop -n sentry-otel-workshop rollout status deployment/notification-worker --timeout=120s
```

Record the catalog pod identity, replace its pod, and read its new identity:

```sh
.local/bin/kubectl --kubeconfig .local/kubeconfig --context kind-sentry-otel-workshop -n sentry-otel-workshop get pods -l app.kubernetes.io/name=catalog -o custom-columns=NAME:.metadata.name,UID:.metadata.uid
.local/bin/kubectl --kubeconfig .local/kubeconfig --context kind-sentry-otel-workshop -n sentry-otel-workshop rollout restart deployment/catalog
.local/bin/kubectl --kubeconfig .local/kubeconfig --context kind-sentry-otel-workshop -n sentry-otel-workshop rollout status deployment/catalog --timeout=120s
.local/bin/kubectl --kubeconfig .local/kubeconfig --context kind-sentry-otel-workshop -n sentry-otel-workshop get pods -l app.kubernetes.io/name=catalog -o custom-columns=NAME:.metadata.name,UID:.metadata.uid
```

Run another Complete journey and compare the old/new application pod UID and name in Sentry with these values.

Checkpoint: one terminal business effect after redelivery, and telemetry reflects the replacement application pod.

## Lab 7 — sample routine infrastructure logs

Prerequisites: working cluster, authenticated Sentry verification access, and `make verify-collector` passing. Run from the repository root:

```sh
make demo-infra-sampling
```

The command compares 100% and 5% sampling with separate marked batches of 2,000 native PostgreSQL routine log messages. It temporarily updates the gateway's sample-rate Secret and restarts that Deployment, then restores the original configured rate in `finally`; it does not edit root `.env`. Both generators terminate. Evidence, generated/exported counts, markers, and assertion results are recorded in ignored `.local/verification/infra-sampling.json`.

Inspect the marks in Sentry Logs under the infrastructure project. Compare the full baseline with approximately 5% retention; this is a statistical rate, not exactly 100 of 2,000 records. Application logs and traces remain at 100%. Infrastructure warnings/errors and recognized PostgreSQL slow-query/lock-wait records have explicit keep priority, including at the 5% rate.

To choose the normal rate, set `OTEL_INFRA_LOG_SAMPLE_PERCENT` in ignored `.env` to `5`, `10`, or `100`, then `make deploy`. This config describes routine PostgreSQL/Redis logs; retained incident records increase the overall proportion. Main Sentry Exporter and static OTLP configurations use the same policy. Tail sampling selects traces and is not the log-sampling mechanism used here.

Checkpoint: expected generated counts, lower routine-log retention, no missing marked application/incident logs, and the original configured rate restored. For existing endpoint credentials with no discovery token, use static mode; Sentry API verification still requires read permission.

## Lab 8 — produce a genuine Redis busy warning

This bounded fault is an attendee exercise, outside the timed presentation. Save any active run identifiers, then run:

```sh
make demo-redis-busy
```

The fixed read-only Lua script runs for approximately 400 ms. Redis's workshop `busy-reply-threshold` is 100 ms, so the server emits a native slow-script warning. It does not write keys, mutate inventory, or send notifications. The generator exits after one invocation; Redis resumes normal processing afterward.

Find the genuine warning in Sentry's infrastructure project. Inspect `resource.service.name:redis`, the real Redis pod identity, severity, and incident timestamp. Native Redis logs have no OTLP trace/span context. Sentry may assign a standalone ingestion trace ID with no `span_id`; that ID is not evidence of an application-trace link. Use pod identity and time to investigate rather than inventing correlation. Warnings remain at 100% despite routine-log sampling.

Inspect `.local/verification/redis-busy.json`, then run Complete journey. Confirm Redis PING succeeds, Stream data remains intact, the pod did not restart, and the healthy workflow completes. `make verify-infra` records the combined infrastructure acceptance in `.local/verification/infra-live.json`.

Checkpoint: a retained native Redis warning with authentic metadata, a terminating generator, and immediate recovery without restart or data loss.

## Finish

`make down` stops the project port-forward. `make cluster-delete` removes only its named cluster. Never use Docker prune or stop a shared runtime to clean up this workshop.

## Telemetry visibility windows

Live checks default to `WORKSHOP_VERIFICATION_TIMEOUT_SECONDS=120`, allowing integer values 30–600. A visibility timeout is a failed check, even if the business workflow succeeds. During rehearsal, Sentry native-log visibility exceeded 120 seconds; later explicit 300-second checks observed all required predicates in both exporter routes. Transport versus indexing delay was not determined. Preserve that distinction in your evidence.

```sh
WORKSHOP_VERIFICATION_TIMEOUT_SECONDS=300 make verify-sentry
WORKSHOP_VERIFICATION_TIMEOUT_SECONDS=300 make verify-infra
```

The selected window is recorded in private evidence. Preflight before presenting and keep already visible current-deployment evidence available; do not wait for telemetry visibility inside the timed demo. This visibility setting does not change application deadlines, collector retry limits, or source-map processing timeouts.
