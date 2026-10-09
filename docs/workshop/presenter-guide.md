# Presenter guide — 60 minutes

## Before the session

1. Confirm the selected Docker context is healthy. Run `make doctor`, `make up`, `make verify` and `make verify-sentry`.
2. Run `make reset-demo`, then one full journey to warm startup paths. Reset again if a clean run list is preferred.
3. Open the control centre and Sentry Issues/Traces/Logs. Filter to the workshop environment and project set.
4. Keep the collector configurations, Kubernetes manifests and `proto/workshop.proto` ready to show. Use the exact current run/trace IDs from the UI, not old rehearsal links.
5. Check source-map uploads and trigger fresh browser/server/worker errors if code changed. Keep local tokens off screen.

## 0–5 minutes: why connected context

Show the diagram and explain the three questions: delivery from Kubernetes, exact-span error linking, and locating performance/failure causes. Distinguish the SDK error path from the Collector OTLP path. State the supported signals: traces and logs.

## 5–15 minutes: collection and routing

Show named cluster, namespaces, Services and pod labels. Show agent DaemonSet, read-only log mount, OTLP receiver and original-pod UID association. Then show gateway project mapping and export configuration. Explain that the API credential discovers per-project ingest endpoints; it is not sent from browser code.

Show static `otlphttp` configuration as the alternative. Explain that only one route is enabled at a time. Avoid spending the session watching initial image downloads: provision during rehearsal.

## 15–25 minutes: healthy journey and cache

Run Full journey. Copy the trace ID, open its Sentry trace, and identify browser/Next/Python/Go/worker spans and database/cache boundaries. Run Cache miss → hit. Compare the two catalog lookups and their correlated logs; show that the second lookup avoids PostgreSQL.

## 25–35 minutes: a slow database

Run Slow PostgreSQL. Show overall duration, then the database query span. The delay is an actual `pg_sleep(1.2)` query against PostgreSQL. Use the surrounding logs to connect lookup start/completion to that span. The control centre shows final workflow state independently of Sentry ingestion.

## 35–45 minutes: errors in context

Run Python catalog error, then Go fulfillment error. Open each originating issue and jump to the associated trace and logs. Show Python's error before order creation; show Go's reservation rollback. Use Next/server and browser errors as additional examples if time permits. Upstream services report failure without duplicating the originating issue.

## 45–55 minutes: async work and Kubernetes context

Run async Recover, then Exhaust. Inspect three attempts in the original trace. Exhaust creates one terminal Node issue and one dead-letter entry. Explain parent context persisted through outbox and Stream message.

Replace the catalog pod from the repository root, then run Full journey again:

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

Use `make reset-demo` between demonstrations if fixtures or run history distract. For an ingestion problem, check readiness, agent parsing, gateway export and project mapping in that order. Do not delete the cluster during a live session unless restarting the entire environment is intended.
