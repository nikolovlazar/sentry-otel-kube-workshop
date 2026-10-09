# Attendee guide

Start from the working baseline. The exercises inspect and alter configuration; they do not require writing a storefront or implementing a new service.

## Lab 1 — bring up the cluster

Prerequisites: Docker-compatible runtime, own Sentry organization/team and five project DSNs. Fill ignored `.env`; choose a read-capable collector token or static exporter mode. Run `make up`, then `make verify`.

Checkpoint: localhost control centre loads, all workloads are ready, Full journey reaches `completed`. Inspect the named context using `.local/bin/kubectl --kubeconfig .local/kubeconfig --context kind-sentry-otel-workshop get pods -A`.

## Lab 2 — follow collection

Read `infra/collectors/agent.yaml`, `gateway-sentry.yaml`, and the collector manifests. Identify the log mount, native trace fields, original-pod association, memory limiter, batching and service-to-project mapping.

Run one workflow and find its exact run ID in Sentry Logs. Compare its log trace ID with the UI. Inspect application pod UID/name and confirm they do not identify the collector.

Checkpoint: a log opens in the same trace as the application span.

## Lab 3 — switch export routes

Change only ignored `.env`: `SENTRY_EXPORTER_MODE=otlphttp`. Run `make deploy`; trigger a fresh workflow and verify its telemetry. Restore `sentry` mode and redeploy if you have the token.

Checkpoint: project destination and application behavior remain the same. There is one active exporter route, and records are not doubled.

## Lab 4 — error plus trace plus logs

Run Python catalog error. Open the issue, inspect the source line, open its trace, and filter logs to the run. Repeat with Go fulfillment error and confirm inventory rollback using `make verify`.

Checkpoint: one originating SDK error event per run, active span correlation, and diagnostic logs around the failure.

## Lab 5 — deliberately break propagation

Apply the supplied patch from the repository root:

```sh
.local/bin/kubectl --kubeconfig .local/kubeconfig --context kind-sentry-otel-workshop -n sentry-otel-workshop patch deployment web --type=strategic --patch-file infra/exercises/propagation/break.yaml
.local/bin/kubectl --kubeconfig .local/kubeconfig --context kind-sentry-otel-workshop -n sentry-otel-workshop rollout status deployment/web
```

Trigger Complete journey. The catalog's HTTP request becomes a separate trace because its carrier is intentionally omitted. Compare the web and catalog spans by run ID in Sentry. Restore the carrier:

```sh
.local/bin/kubectl --kubeconfig .local/kubeconfig --context kind-sentry-otel-workshop -n sentry-otel-workshop patch deployment web --type=strategic --patch-file infra/exercises/propagation/restore.yaml
.local/bin/kubectl --kubeconfig .local/kubeconfig --context kind-sentry-otel-workshop -n sentry-otel-workshop rollout status deployment/web
```

Trigger another journey. This configuration flag is internal to the exercise; it is never a control-centre input. `make verify-sentry` must fail its service coverage check while propagation is broken.
Checkpoint: broken carrier produces a split; restoring propagation reconnects the trace. Do not change the Sentry project mapping or SDK initialization for this exercise.

## Lab 6 — async and pod identity

Compare worker Recover and Exhaust modes. Inspect attempts and consumer parentage. Run the deterministic crash check from the repository root after installing the [developer test prerequisites](../../README.md#developer-tests):

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

Run another Full journey and compare the old/new application pod UID and name in Sentry with these values.

Checkpoint: one terminal business effect after redelivery, and telemetry reflects the replacement application pod.

## Finish

`make down` stops the project port-forward. `make cluster-delete` removes only its named cluster. Never use Docker prune or stop a shared runtime to clean up this workshop.
