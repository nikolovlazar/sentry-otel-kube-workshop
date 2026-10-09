# Control centre

Next.js is the browser UI and BFF. It initializes its own OTel provider before Sentry, exports server spans through the in-cluster collector, and sends Sentry SDK errors directly. Browser configuration comes from `/api/public-config` at runtime; image builds contain no DSNs or upload tokens.

The BFF exposes `POST /api/runs`, `GET /api/runs?sessionId=...`, `GET /api/runs/:runId`, and `POST /api/runs/:runId/browser-error`. Readiness checks catalog HTTP and fulfillment gRPC. Origin errors from Python/Go are recorded using their event IDs without capturing the same error again in the BFF.

Run `npm ci`, `npm run typecheck`, `npm test`, and `npm run build`. Runtime variables: `SENTRY_DSN`, optional `SENTRY_BROWSER_DSN`, `SENTRY_ORG_SLUG`, `SENTRY_BASE_URL`, `SENTRY_RELEASE`, `WORKSHOP_ENVIRONMENT`, `WORKSHOP_SESSION_ID`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `FULFILLMENT_ADDR`, `CATALOG_URL`, and `WORKSHOP_PROTO_PATH`.

Source maps are generated through `withSentryConfig`. Uploads are disabled when the private build-only `SOURCE_MAP_AUTH_TOKEN` is absent; root release tooling can inject/upload the production build separately. Never put tokens in client variables or Docker build arguments.

The internal attendee-lab switch `WORKSHOP_BREAK_CATALOG_PROPAGATION=true` deliberately omits the outgoing catalog W3C carrier while preserving the request and its run/session identifiers. It logs a warning and does not create a Sentry exception. Unset it or set it to `false` to restore the complete trace.

Workflow identity (`workshop.run_id`, `workshop.session_id`, `workshop.scenario`) travels in W3C baggage. The provider copies those scalar fields onto every span created with that workflow context, including library spans. Logs use the same baggage fallback. Framework spans started before request parsing or run creation cannot have a run ID yet; workflow spans and downstream calls acquire it as soon as the run is created. The browser action span receives the returned run ID after the initial request completes.
