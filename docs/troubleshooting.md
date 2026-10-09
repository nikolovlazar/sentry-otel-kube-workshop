# Troubleshooting

## Start with application state

Run `make doctor` and `make verify`. Use the repository kubeconfig and named context on every command. A healthy HTTP response does not prove Sentry ingestion; use `make verify-sentry` for that layer.

## App not ready

Inspect Deployment/StatefulSet readiness, migration Job completion, database roles and Redis password configuration. A fresh database must initialize roles before schema migrations. Existing PVCs preserve passwords/data; reset demo data through the dedicated command.

## App works, no traces

Check that OTel initialization precedes application loading, sampling is enabled and OTLP endpoint is the agent Service. Receiver ports are 4317 (gRPC) and 4318 (HTTP). Inspect agent/gateway logs for validation, DNS, authorization and project-mapping failures. Ensure all resources have `service.name`.

## Logs arrive but do not connect

Inspect the original JSON stdout line. `trace_id` must have 32 hexadecimal characters and `span_id` 16. The agent must parse these into OTLP LogRecord context fields, not retain them only as attributes. Verify the log was emitted while the intended span was active.

## Wrong pod or project

Associate Kubernetes resources by originating pod UID; log-file paths provide resource identity. Check pod labels and explicit service-to-project mappings. Infrastructure logs without workflow context are expected to remain untraced.

## Errors arrive without the OTel span

Check the language linking integration and active context at capture time. Go requires EventHint.Context. Python must not replace our W3C propagator or add a second exporter. Backend JavaScript must not enable another Sentry tracing pipeline. Browser propagation must include `traceparent`.

## Expired presenter CLI token

Refresh/login through the supported `sentry` CLI, then privately refresh ignored `.env` credentials and redeploy the gateway Secret. Do not paste the token in chat, commit it, or log it. Static mode can route existing project ingest keys without this management credential.

## Minified JavaScript frames

Confirm maps correspond to the exact assets in the running images. Injection after building/running the image changes IDs and cannot repair matching. Upload maps for the current debug IDs/release, then trigger a fresh error; historical events do not automatically acquire new source context.

## Package mirror rejects a dependency

Read the mirror's stated reason. Use a supported dependency version approved by that mirror or involve its administrators. Preserve the security policy. The catalog uses an HTTP-only exporter dependency set and a pinned compatible Pydantic version.

## Pending worker messages

Inspect run state, attempt count and consumer group pending entries. A message remains pending until terminal state is recorded. The worker claims interrupted entries and deduplicates terminal effects. `make reset-demo` stops application writers before clearing only workshop keys/tables.
