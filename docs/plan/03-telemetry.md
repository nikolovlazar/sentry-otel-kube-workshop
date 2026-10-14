# Telemetry ownership and linking

## Decisions

OTel owns backend tracing and sampling. Keep application traces and logs at 100%, with health exclusions. Sentry owns SDK error capture. W3C TraceContext+Baggage cross HTTP/gRPC/Redis; browser propagateTraceparent is enabled.

## Steps

Initialize providers before apps; configure current language event-linking integrations; add business/client spans and structured stdout; restrict data collection; release/source-map setup.

## Acceptance

Same trace ID across runtimes and queue; every SDK error links to its active span; every workflow log retains native OTLP context; source locations readable.

## Completion checklist

- [x] Implementation complete
- [x] Automated checks complete
- [x] Live verification recorded where applicable
- [x] Documentation matches observed behavior

## Prerequisites, commands and expected results

Start the configured cluster with `make up`; project DSNs, service mappings and the separate upload token must be set. Run `make verify-sentry` with the [verification read scopes](04-collector-and-sentry.md#credential-scope-by-purpose), then trigger the browser exception in the UI. Expect shared trace IDs, exact origin-error span IDs, native log context, original application pod metadata and readable source. Use the runtime initialization and log-schema sections below to locate a failed assertion.

## Ownership by runtime

Next/worker initialize OTel NodeSDK and W3C composite propagator; Sentry uses `enableOpenTelemetrySetup:false` plus `openTelemetryIntegration()`. Server Sentry tracesSampleRate/tracesSampler remain unset. Browser uses Sentry tracing100% and `propagateTraceparent:true`.

Python configures TracerProvider+HTTP exporter and disables OTLPIntegration exporter/propagator setup. Its explicit dependency set includes only the HTTP exporter used by that integration. Go uses normal OTel exporter+NewOtelIntegration; EventHint.Context carries the active span during error capture.

## Signal fields

Service resource: name/namespace/version/environment and podUID/name/namespace/node. Workflow span/log/error tags: workshop.run_id,session_id,scenario,step,attempt where meaningful. W3C carrier survives HTTP, gRPC and transactional outbox/Stream boundaries.

JSON stdout keys: timestamp,severity,body,service.name,log.record_id,trace_id,span_id,trace_flags,workshop.*. One stdout path; SDK log exporting is disabled. Span events alone are not Issues. Errors are captured once at origin; retries log each attempt and capture only terminal exhaustion.

Release/version labels must match the running code. JavaScript debug IDs are injected during `npm run build`. The same build uploads maps for those exact assets and waits for Sentry processing before the workshop images are deployed. Inspect fresh SDK errors for source context.

## Runtime initialization and ownership

1. Next.js `src/instrumentation.ts` initializes the OTel NodeSDK before Sentry. `enableOpenTelemetrySetup:false` and `openTelemetryIntegration()` prevent an overlapping Sentry provider/exporter. The worker uses the same arrangement.
2. Python creates its own provider/exporter/instrumentations, then adds Sentry `OTLPIntegration` with its automatic exporter/propagator setup disabled.
3. Go registers its own OTel provider and `NewOtelIntegration`; error capture receives the active request context.
4. Browser Sentry initializes from the public runtime configuration endpoint, traces at100%, and sets `propagateTraceparent:true`. Its propagation targets are the app origin/relative paths.

Backend SDK tracing rates/samplers stay unset. OTel uses an AlwaysOn sampler with W3C TraceContext and Baggage. Each outgoing HTTP/gRPC carrier and queued message carries this context. Business spans are explicit; database/Redis client spans describe dependencies. `SERVICE_VERSION` comes from the Git commit; the common SDK release uses that revision. Environment is `workshop-local`.

## Log schema and correlation

JSON stdout contains timestamp, severity, body, `log.record_id`, service, `trace_id`, `span_id`, `trace_flags` and scalar workshop metadata. Resource attributes include service namespace/version, environment, original pod UID/name, namespace and node. Error logs use the same active span as SDK capture. Startup and infrastructure logs outside an active workflow can be untraced. Kubernetes pod metadata applies to in-cluster workloads; browser telemetry is outside the cluster and shares the web Sentry project without a pod UID.

The agent parses container framing and JSON, normalizes trace flags to a byte string, and assigns native OTLP LogRecord TraceID/SpanID fields. Ordinary attributes named trace_id cannot establish native correlation. Only stdout exports application logs: SDK logging and SDK OTLP log exporters stay disabled.

Use `make verify-sentry` after `make up` or `make build` plus `make deploy`. Automatic build-time uploads must finish processing first. SDK errors must resolve to the same trace and an existing span in the corresponding workload project. The optional `make source-map-upload` recovery command verifies the exact running images before re-uploading; old events are not proof of new source-map processing. Worker delivery preserves the original error stack and consumer span even after restart.

Validated workflow metadata also travels in W3C baggage. Small span processors copy the three allowlisted identifiers into child spans; logs use the same context. Automatic frontend/framework spans that begin before the request body has been parsed cannot know a run ID yet; explicit business spans and subsequent dependency spans carry it. `workshop.step` names the active operation, with attempt numbers on consumer spans.

## Health-check exclusion

Next.js drops server spans for `/health/live` and `/health/ready` at sampling time. Locally unsampled parents also keep their framework descendants unsampled, so excluding only the named route cannot leave orphan background spans. Normal workshop requests retain 100% sampling, including incoming unsampled remote carriers. Health responses and Kubernetes probes remain enabled. Python already excludes its health routes; Go and worker dependency/queue housekeeping remain untraced.

Run `npm --prefix apps/web test` for probe/descendant and normal-workflow regression tests. After rebuild/deployment, verify live readiness, observe a quiet idle interval and trigger a normal distributed workflow. The private follow-up record is `.local/verification/health-checks.json`.

## Native PostgreSQL and Redis logs

Application JSON logs continue through their sole stdout path at 100% retention. The same agent parses native PostgreSQL and Redis container output, preserving `service.name`, original pod identity, timestamps, severities, and component fields. Collector logs remain excluded.

PostgreSQL logs to stderr with slow-statement threshold 500 ms and lock-wait threshold 250 ms. Its prefix includes application name, process ID, and SQLSTATE; bind-parameter logging is disabled. Catalog supplies `w:<trace-id>:<span-id>` as connection `application_name` while `catalog.product_query` is active. Only validated identifiers are converted to native OTLP trace/span fields. These records identify the actual PostgreSQL service/pod and link to the enclosing business span; they do not claim the database server emitted a new trace span.

Redis native server records contain no OTLP trace/span context unless the server supplies it. The collector fixtures verify empty native context. Sentry may display a standalone trace ID on ingestion even though `span_id` is absent; that ID does not establish a link to an application trace. Use `resource.service.name:redis`, pod identity, and incident timestamps to investigate them. Application Redis client spans and Redis native logs describe different layers.

## Infrastructure-only log sampling

The gateway probabilistically retains routine PostgreSQL and Redis logs at `OTEL_INFRA_LOG_SAMPLE_PERCENT=5`, accepting only 5, 10, or 100. Hash a unique per-record identifier assigned before agent export so retry delivery keeps the same decision. Explicit sampling priority 100 preserves application records, infrastructure warnings/errors, recognized PostgreSQL slow-query/lock-wait evidence, and components outside the sampled pair. Both exporter configurations apply identical log processing.

The percentage applies to routine infrastructure logs. Retained warnings and incidents increase total retention. Application trace sampling remains 100%; health checks remain excluded. Tail sampling chooses traces and is not this log policy. No trace tail sampler is introduced.

Run `make verify-collector` for parsing/context/sampler fixtures, `make verify-infra` for native Sentry evidence, and `make demo-infra-sampling` for a finite 2,000-record 100%/5% comparison. New live acceptance is recorded separately from initial checked milestones.
