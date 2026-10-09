# Collector and Sentry

## Decisions

Agent DaemonSet collects OTLP traces and container stdout. Gateway routes to five pre-created Sentry projects. Sentry Exporter is main; static otlphttp is an exclusive alternative.

## Steps

Provision projects through plugin; fill local placeholders; parse container+JSON+trace fields; associate original pod UID; map services to projects; configure memory limits, queues and batch.

## Acceptance

No missing service names, feedback loops or duplicate paths. Exporter validation passes; actual traces/logs reach correct projects; restart resumes collection. Recovery assertions distinguish agent-to-gateway retries from static OTLP/HTTP remote-endpoint retries.

## Completion checklist

- [x] Implementation complete
- [x] Automated checks complete
- [x] Live verification recorded where applicable
- [x] Documentation matches observed behavior

## Prerequisites

The five projects and ignored `.env` must be configured before the commands below. Create the named local cluster with `make cluster-up`, or use `make up` for the complete bootstrap/build/deploy/verification sequence. Read credentials belong to discovery/verification; the separate upload token is required for workshop builds.

## Provision and configure

Create five named projects on the selected Sentry team using the Sentry plugin. Record actual DSNs only in ignored `.env`. SDK DSNs and service mapping must target the same projects. Static alternative derives OTLP endpoints/public auth headers from project DSNs.

The Sentry Exporter uses `auto_create_projects:false`, routes by `service.name` and requires Organization Read+Project Read. Presenter may privately reuse an authenticated CLI credential; attendee limited Internal Integration is the least-permission setup. All examples contain placeholders.

## Collector stages

Agent: OTLP receiver + filelog container parser; JSON body/severity/native trace fields; original pod association and labels; resource attributes; bounded memory and batching; OTLP gateway export. Include only workshop pod logs and exclude collector logs. Persist checkpoints.

Gateway: OTLP receiver, bounded memory, removal of auth/cookie/connection-string fields, batching and exclusive Sentry/static export route. Persist a bounded sending queue of unsent batches; retry behavior depends on the chosen exporter. Synthetic parameterized SQL text can remain for explaining latency. Debug export is a temporary diagnostic configuration, not a parallel production path.

Missing resource service names and unknown mappings fail verification. Trace context on logs must occupy native fields. Current constraints: OTLP ingestion beta, Sentry Exporter alpha, no OTLP metrics.

## Project routing and credentials

| Resource service | Sentry project suffix |
| --- | --- |
| web and browser | web |
| catalog | catalog |
| fulfillment | fulfillment |
| notification-worker | worker |
| edge, postgres, redis | infrastructure |

All suffixes follow `sentry-otel-workshop-`. Provision the projects on the requested team with the Sentry plugin; obtain each DSN and keep it only in `.env`. The same project receives the SDK error and the collector's spans/logs. Among cluster workloads, `SENTRY_AUTH_TOKEN` is read by the gateway only; upload credentials are never deployed or baked into images.

1. Select `SENTRY_EXPORTER_MODE=sentry` or `otlphttp`. Run `make configure` to create ignored Secrets/configuration and validate the selected collector pipelines.
2. Run `make build` then `make deploy`, or use `make up` for the full sequence. The JavaScript builds upload maps and wait for processing before deployment. The agent's OTLP ports are4317/4318. Applications use HTTP4318; the agent forwards gRPC4317 to the gateway.
3. Run `make verify-sentry`. Require the intended project coverage, correlated logs, Kubernetes context and SDK errors within120s.
4. To switch, change only the exporter mode and redeploy; the gateway has a single active route, so there is no parallel delivery. Restore the original mode and repeat a marked workflow.

The static gateway uses routing connectors and one authenticated `otlphttp` exporter per project. Endpoints are derived from DSNs as `/api/<project-id>/integration/otlp`; project public ingest keys provide authentication. No management token is needed in this mode.

The main exporter uses the Sentry API for project/key discovery, explicit mappings and `auto_create_projects:false`. Its token needs Organization Read and Project Read. Persistent gateway queues are bounded; the agent has persistent file-reader checkpoints. Collector logs are excluded from the agent glob to prevent loops. SQL text is synthetic/parameterized; authentication, cookies and connection strings are removed.

## Retry and persistence limits

The pinned Contrib Sentry Exporter 0.162.0 uses `WithQueue` for traces and logs but never calls `WithRetry`. The exporter helper defaults to retries disabled. Its configuration exposes `sending_queue` but no `retry_on_failure`; adding that key is rejected. Main mode preserves unsent queued batches across gateway restarts, but a failed Sentry HTTP batch is not retried and can be dropped. [Sentry Exporter factory](https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/v0.162.0/exporter/sentryexporter/factory.go), [exporter helper](https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector/v0.162.0/exporter/exporterhelper/internal/base_exporter.go), [Sentry Exporter configuration](https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/v0.162.0/exporter/sentryexporter/config.go).

The agent's `otlp/gateway` explicitly retries transient gateway failures for up to 60 seconds; its sending queue is in memory. A gateway-down rehearsal proves this hop only. Static `otlphttp` mode has a persistent queue and explicit transient-failure retries for up to 120 seconds. Test a blocked remote endpoint and restart recovery in that mode, then restore main mode. Neither bounded queues nor bounded retries guarantee delivery through an indefinite outage.

## Credential scope by purpose

| Purpose | Configuration | Permissions |
| --- | --- | --- |
| Sentry Exporter project discovery | `SENTRY_AUTH_TOKEN` in `.env`, deployed only to gateway | Organization Read (`org:read`) and Project Read (`project:read`) |
| Automated trace/log/error verification | `SENTRY_AUTH_TOKEN` for the local verification process | Collector read scopes plus Events Read (`event:read`) |
| JavaScript source-map upload | Separate `SENTRY_SOURCE_MAP_AUTH_TOKEN` in ignored root `.env`; BuildKit secret during Docker builds | A CI/upload token with `org:ci`; legacy personal-token uploads use `org:read` plus `project:releases` and access to the selected projects |

These scope names follow [Sentry's permissions reference](https://docs.sentry.io/api/permissions/). Static `otlphttp` export needs project ingest keys but no discovery token; the authenticated verification API still needs read access.

To keep a limited discovery token in `.env`, override it for this one verification process with an authenticated presenter CLI credential:

```sh
SENTRY_AUTH_TOKEN="$(sentry auth token)" make verify-sentry
```

Command substitution supplies the token without printing it. Run with shell tracing disabled. This does not redeploy or change the gateway Secret. Both JavaScript `npm run build` wrappers read ignored root `.env`, honor explicit environment overrides, compile/inject debug IDs, upload matching maps and wait up to 120 seconds for processing. The full `make doctor`/`make build`/`make up` path requires the upload token and fails when upload or processing fails. An isolated npm build with no token compiles with an explicit skipped-upload message. Docker supplies the token only as the `sentry_source_map_token` BuildKit secret; routing/release parameters and a fresh upload nonce are nonsecret build arguments. The token is never an image build argument, image environment variable or Kubernetes Secret. `make source-map-upload` remains a guarded recovery utility for the exact deployed images.

The build wrapper selects credentials in this order: the mounted BuildKit secret; an explicit `SENTRY_SOURCE_MAP_AUTH_TOKEN` (or legacy `SOURCE_MAP_AUTH_TOKEN`); an explicit caller `SENTRY_AUTH_TOKEN`; then the upload-specific token in root `.env`. The collector's `SENTRY_AUTH_TOKEN` stored in root `.env` is never an upload fallback. The compiler and debug-ID injection children receive none of these auth variables; only the upload child receives the selected credential as `SENTRY_AUTH_TOKEN`. For an isolated host build, `SENTRY_UPLOAD_SOURCE_MAPS=false` explicitly skips uploading; required workshop builds reject that setting.
