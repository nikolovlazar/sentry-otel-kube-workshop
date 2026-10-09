# Presenter guide — 60 minutes

## Before the session

1. Start the selected Docker runtime and fill ignored `.env` with project DSNs, collector/read credentials and the separate source-map upload token. For a fresh checkout, run `make bootstrap` before `make doctor`.
2. Run `make up`, then `make verify` and `make verify-sentry` in that order. Both JavaScript builds upload matching maps and wait for Sentry processing before deployment; failed upload/processing stops the build. The [credential table](../plan/04-collector-and-sentry.md#credential-scope-by-purpose) explains verification-only token overrides.
3. Run `make reset-demo`, reload the control-centre page to obtain the new session, then run one Complete journey to warm startup paths. Reset and reload again if a clean run list is preferred.
4. Open the control centre and Sentry Issues/Traces/Logs. Filter to the workshop environment and project set.
5. Keep the collector configurations, Kubernetes manifests and `proto/workshop.proto` ready to show. Use the exact current run/trace IDs from the UI, not old rehearsal links.
6. Rebuild/redeploy if code changed, then check fresh browser/server/worker errors for original source context. Use `make source-map-upload` only for guarded recovery of the exact running images. Keep local tokens off screen.

## 0–5 minutes: why connected context

Show the diagram and explain the three questions: delivery from Kubernetes, exact-span error linking, and locating performance/failure causes. Distinguish the SDK error path from the Collector OTLP path. State the supported signals: traces and logs.

## 5–15 minutes: collection and routing

Show named cluster, namespaces, Services and pod labels. Show agent DaemonSet, read-only log mount, OTLP receiver and original-pod UID association. Then show gateway project mapping and export configuration. Explain that the API credential discovers per-project ingest endpoints; it is not sent from browser code.

Show static `otlphttp` configuration as the alternative. Explain that only one route is enabled at a time. Main mode has persistence for unsent batches but no retry of failed Sentry HTTP deliveries; static mode explicitly enables bounded retries. A gateway-down rehearsal proves the agent hop only; see [collector recovery limits](../plan/04-collector-and-sentry.md#retry-and-persistence-limits). Avoid spending the session watching initial image downloads: provision during rehearsal.

## 15–25 minutes: healthy journey and cache

Run Complete journey. Copy the trace ID, open its Sentry trace, and identify browser/Next/Python/Go/worker spans and database/cache boundaries. Run Cache miss → hit. Compare the two catalog lookups and their correlated logs; show that the second lookup avoids PostgreSQL.

## 25–35 minutes: a slow database

Run Slow database. Show overall duration, then the database query span. The delay is an actual `pg_sleep(1.2)` query against PostgreSQL. Use the surrounding logs to connect lookup start/completion to that span. The control centre shows final workflow state independently of Sentry ingestion.

## 35–45 minutes: errors in context

Run Python exception, then Go exception. Open each originating issue and jump to the associated trace and logs. Show Python's error before order creation; show Go's reservation rollback. Use Next/server and browser errors as additional examples if time permits. Upstream services report failure without duplicating the originating issue.

## 45–55 minutes: async work and Kubernetes context

Run Background job with Recover on attempt 3, then Exhaust → dead letter. Inspect three attempts in the original trace. Exhaust creates one terminal Node issue and one dead-letter entry. Explain parent context persisted through outbox and Stream message.

Replace the catalog pod from the repository root, then run Complete journey again:

```sh
.local/bin/kubectl --kubeconfig .local/kubeconfig --context kind-sentry-otel-workshop -n sentry-otel-workshop get pods -l app.kubernetes.io/name=catalog -o custom-columns=NAME:.metadata.name,UID:.metadata.uid
.local/bin/kubectl --kubeconfig .local/kubeconfig --context kind-sentry-otel-workshop -n sentry-otel-workshop rollout restart deployment/catalog
.local/bin/kubectl --kubeconfig .local/kubeconfig --context kind-sentry-otel-workshop -n sentry-otel-workshop rollout status deployment/catalog --timeout=120s
.local/bin/kubectl --kubeconfig .local/kubeconfig --context kind-sentry-otel-workshop -n sentry-otel-workshop get pods -l app.kubernetes.io/name=catalog -o custom-columns=NAME:.metadata.name,UID:.metadata.uid
```

Compare old/new pod names and UIDs in Sentry. The collectors must attribute telemetry to the application pod. During rehearsal, run `make verify-recovery` to exercise a controlled worker crash and SDK outage with disposable Redis and local fake gRPC/Sentry receivers; see attendee Lab 6 for its assertions.

## 55–60 minutes: questions

Point attendees to the exercises and troubleshooting. Explain current beta/alpha boundaries and the absence of OTLP metrics support without promising infrastructure monitoring features.

## Recovery

Use `make reset-demo` between demonstrations if fixtures or run history distract, then reload the control-centre page for the new session. For an ingestion problem, check readiness, agent parsing, gateway export and project mapping in that order. Do not delete the cluster during a live session unless restarting the entire environment is intended.
