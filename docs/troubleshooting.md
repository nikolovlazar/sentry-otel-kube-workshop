# Troubleshooting

## Start with application state

Run `make doctor` and `make verify`. Use the repository kubeconfig and named context on every command. A healthy HTTP response does not prove Sentry ingestion; use `make verify-sentry` for that layer.

## App not ready

Inspect Deployment/StatefulSet readiness, migration Job completion, database roles and Redis password configuration. A fresh database must initialize roles before schema migrations. Existing PVCs preserve passwords/data; reset demo data through the dedicated command.

## App works, no traces

Check that OTel initialization precedes application loading, sampling is enabled and OTLP endpoint is the agent Service. Receiver ports are 4317 (gRPC) and 4318 (HTTP). Inspect agent/gateway logs for validation, DNS, authorization and project-mapping failures. Ensure all resources have `service.name`.

## Collector HTTP failures during an outage

In main mode, the pinned Sentry Exporter does not retry failed Sentry HTTP batches. Its persistent queue preserves unsent work across restart, not failed-batch delivery. Adding `retry_on_failure` to this exporter is rejected. The agent's retry applies only to its gateway hop. Static OTLP/HTTP mode enables bounded remote retries; inspect queue/export failure logs and run fresh verification after recovery. See [collector recovery limits](plan/04-collector-and-sentry.md#retry-and-persistence-limits).

## Logs arrive but do not connect

Inspect the original JSON stdout line. `trace_id` must have 32 hexadecimal characters and `span_id` 16. The agent must parse these into OTLP LogRecord context fields, not retain them only as attributes. Verify the log was emitted while the intended span was active.

## Wrong pod or project

Associate Kubernetes resources by originating pod UID; log-file paths provide resource identity. Check pod labels and explicit service-to-project mappings. Infrastructure logs without workflow context are expected to remain untraced.

## Errors arrive without the OTel span

Check the language linking integration and active context at capture time. Go requires EventHint.Context. Python must not replace our W3C propagator or add a second exporter. Backend JavaScript must not enable another Sentry tracing pipeline. Browser propagation must include `traceparent`.

## Expired presenter CLI token

Refresh/login through the supported `sentry` CLI, then privately refresh ignored `.env` credentials and redeploy the gateway Secret. Do not paste the token in chat, commit it, or log it. Static mode can route existing project ingest keys without this management credential.

## Minified JavaScript frames

Both JavaScript builds automatically inject debug IDs and upload/process their matching maps before workshop deployment. Confirm the configured release matches the running images and trigger a fresh error; historical events do not automatically acquire new source context. Use the guarded `make source-map-upload` utility to recover matching maps from the exact deployed images. Reinjection after an image is built changes IDs and cannot repair its running assets.

## Package mirror rejects a dependency

Read the mirror's stated reason. Use a supported dependency version approved by that mirror or involve its administrators. Preserve the security policy. The catalog uses an HTTP-only exporter dependency set and a pinned compatible Pydantic version.

## Pending worker messages

Inspect run state, attempt count and consumer group pending entries. A message remains pending until terminal state is recorded. The worker claims interrupted entries and deduplicates terminal effects. `make reset-demo` stops application writers before clearing only workshop keys/tables.

## Reset stopped before completion

Reset scales the four application Deployments to zero before changing fixtures. If a database migration or configuration step fails, they remain stopped so writers cannot race a partial reset. Fix the reported cause, then run `make deploy` to reapply configuration/migrations and restore the Deployments. Run `make verify` afterward. This preserves PVCs and retained Sentry history.

## Runs fail after reset

`make reset-demo` creates a new session and restarts the application pods. An already-open control-centre page still holds the previous session; reload it before launching another workflow.

## Verification returns HTTP403

A minimal collector discovery token need not include Events Read. Use the local verification-only override in the [credential table](plan/04-collector-and-sentry.md#credential-scope-by-purpose), or provide a read token with `event:read` in addition to `org:read`/`project:read`. Static OTLP export can work while authenticated API verification lacks permission.

## Build-time source-map upload fails

The full workshop `make doctor`, `make build` and `make up` path requires `SENTRY_SOURCE_MAP_AUTH_TOKEN`. Check the separate upload token's permissions, selected organization/project and network access; collector read credentials alone cannot upload maps. Both JavaScript npm wrappers wait up to 120 seconds for processing and fail the build if upload or processing fails. Host-build details are written to ignored `.local/verification/build-source-maps-<component>.log`; Docker build output reports the failing step. After fixing the cause, rebuild and deploy before triggering fresh JavaScript errors. An isolated npm build without a token reports that upload was skipped.

If the upload CLI reports a certificate-verification failure in a custom slim builder, check its operating-system CA bundle. The supplied JavaScript build stages install `ca-certificates`; keep TLS verification enabled and add any required trusted proxy CA to the builder's trust store.

## Source-map recovery reports a runtime mismatch

`make source-map-upload` checks every running web/worker pod against the selected local Docker content ID and the shared release/version configuration. It extracts assets from the verified immutable image IDs and rechecks before upload. If a rebuild has not been deployed, wait for both application rollouts after `make build` and `make deploy`, then rerun `make source-map-upload`. Do not upload maps from a different local image to repair a running release.
