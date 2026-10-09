import { NodeSDK } from "@opentelemetry/sdk-node";
import { resourceFromAttributes } from "@opentelemetry/resources";
import {
  AlwaysOnSampler,
  BatchSpanProcessor,
} from "@opentelemetry/sdk-trace-base";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
import {
  CompositePropagator,
  W3CBaggagePropagator,
  W3CTraceContextPropagator,
} from "@opentelemetry/core";
import * as Sentry from "@sentry/node";
import { WorkflowAttributesProcessor } from "./workflow-context.js";
const endpoint = (
  process.env.OTEL_EXPORTER_OTLP_ENDPOINT ?? "http://localhost:4318"
).replace(/\/$/, "");
export const sdk = new NodeSDK({
  resource: resourceFromAttributes({
    "service.name": process.env.OTEL_SERVICE_NAME ?? "notification-worker",
    "service.namespace": "sentry-otel-workshop",
    "service.version":
      process.env.SERVICE_VERSION ??
      process.env.SENTRY_RELEASE ??
      "development",
    "deployment.environment.name":
      process.env.WORKSHOP_ENVIRONMENT ?? "workshop-local",
    "k8s.pod.uid": process.env.POD_UID ?? "",
    "k8s.pod.name": process.env.POD_NAME ?? "",
    "k8s.namespace.name": process.env.POD_NAMESPACE ?? "",
    "k8s.node.name": process.env.NODE_NAME ?? "",
  }),
  sampler: new AlwaysOnSampler(),
  textMapPropagator: new CompositePropagator({
    propagators: [new W3CTraceContextPropagator(), new W3CBaggagePropagator()],
  }),
  spanProcessors: [
    new WorkflowAttributesProcessor(),
    new BatchSpanProcessor(
      new OTLPTraceExporter({ url: `${endpoint}/v1/traces` }),
    ),
  ],
});
sdk.start();
const deliveries = new Map<string, boolean>();
export function beginErrorDelivery(eventId: string) {
  deliveries.delete(eventId);
}
export function errorWasDelivered(eventId: string) {
  return deliveries.get(eventId) === true;
}
Sentry.init({
  dsn: process.env.SENTRY_DSN,
  environment: process.env.WORKSHOP_ENVIRONMENT ?? "workshop-local",
  release: process.env.SENTRY_RELEASE,
  enableOpenTelemetrySetup: false,
  integrations: (defaults) => [
    ...defaults.filter((integration) => integration.name !== "Dedupe"),
    Sentry.openTelemetryIntegration(),
  ],
  dataCollection: {
    userInfo: false,
    cookies: false,
    httpHeaders: false,
    httpBodies: [],
    urlQueryParams: false,
    graphQL: { document: false, variables: false },
    genAI: { inputs: false, outputs: false },
    databaseQueryData: false,
    queues: false,
    stackFrameVariables: false,
  },
});
Sentry.getClient()?.on("afterSendEvent", (event, response) => {
  if (event.event_id)
    deliveries.set(
      event.event_id,
      typeof response.statusCode === "number" &&
        response.statusCode >= 200 &&
        response.statusCode < 300,
    );
});
export async function flushTelemetry() {
  await Promise.allSettled([Sentry.flush(2000), sdk.shutdown()]);
}
