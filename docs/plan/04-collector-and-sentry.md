# Collector and Sentry

## Decisions

Agent DaemonSet collects OTLP traces and container stdout. Gateway routes to five pre-created Sentry projects. Sentry Exporter is main; static otlphttp is an exclusive alternative.

## Steps

Provision projects through plugin; fill local placeholders; parse container+JSON+trace fields; associate original pod UID; map services to projects; configure memory limits, queues and batch.

## Acceptance

No missing service names, feedback loops or duplicate paths. Exporter validation passes; actual traces/logs reach correct projects; restart resumes collection.

## Completion checklist

- [ ] Implementation complete
- [ ] Automated checks complete
- [ ] Live verification recorded where applicable
- [ ] Documentation matches observed behavior

## Provision and configure

Create five named projects on the selected Sentry team using the Sentry plugin. Record actual DSNs only in ignored `.env`. SDK DSNs and service mapping must target the same projects. Static alternative derives OTLP endpoints/public auth headers from project DSNs.

The Sentry Exporter uses `auto_create_projects:false`, routes by `service.name` and requires Organization Read+Project Read. Presenter may privately reuse an authenticated CLI credential; attendee limited Internal Integration is the least-permission setup. All examples contain placeholders.

## Collector stages

Agent: OTLP receiver + filelog container parser; JSON body/severity/native trace fields; original pod association and labels; resource attributes; bounded memory and batching; OTLP gateway export. Include only workshop pod logs and exclude collector logs. Persist checkpoints.

Gateway: OTLP receiver, bounded memory, removal of auth/cookie/connection-string fields, batching and exclusive Sentry/static export route. Persist a bounded sending queue. Synthetic parameterized SQL text can remain for explaining latency. Debug export is a temporary diagnostic configuration, not a parallel production path.

Missing resource service names and unknown mappings fail verification. Trace context on logs must occupy native fields. Current constraints: OTLP ingestion beta, Sentry Exporter alpha, no OTLP metrics.

## Project routing and credentials

| Resource service | Sentry project suffix |
| --- | --- |
| web and browser | web |
| catalog | catalog |
| fulfillment | fulfillment |
| notification-worker | worker |
| edge, postgres, redis | infrastructure |

All suffixes follow `sentry-otel-workshop-`. Provision the projects on the requested team with the Sentry plugin; obtain each DSN and keep it only in `.env`. The same project receives the SDK error and the collector's spans/logs. `SENTRY_AUTH_TOKEN` is read by the gateway only; upload credentials are never deployed or baked into images.

1. Select `SENTRY_EXPORTER_MODE=sentry` or `otlphttp`. Run `make configure` to create ignored Secrets/configuration and validate the selected collector pipelines.
2. Run `make deploy`. The agent's OTLP ports are4317/4318. Applications use HTTP4318; the agent forwards gRPC4317 to the gateway.
3. Run `make verify-sentry`. Require the intended project coverage, correlated logs, Kubernetes context and SDK errors within120s.
4. To switch, change only the exporter mode and redeploy; the gateway has a single active route, so there is no parallel delivery. Restore the original mode and repeat a marked workflow.

The static gateway uses routing connectors and one authenticated `otlphttp` exporter per project. Endpoints are derived from DSNs as `/api/<project-id>/integration/otlp`; project public ingest keys provide authentication. No management token is needed in this mode.

The main exporter uses the Sentry API for project/key discovery, explicit mappings and `auto_create_projects:false`. Its token needs Organization Read and Project Read. Persistent gateway queues are bounded; the agent has persistent file-reader checkpoints. Collector logs are excluded from the agent glob to prevent loops. SQL text is synthetic/parameterized; authentication, cookies and connection strings are removed.
