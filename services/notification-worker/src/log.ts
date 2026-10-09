import { randomUUID } from "node:crypto";
import { context, trace } from "@opentelemetry/api";
import { baggageAttributes } from "./workflow-context.js";
export function log(
  body: string,
  severity = "INFO",
  fields: Record<string, string | number | boolean> = {},
) {
  const activeSpan = trace.getActiveSpan();
  const fallback = {
    ...baggageAttributes(context.active()),
    "workshop.step":
      (activeSpan as { name?: string } | undefined)?.name ?? "application.log",
  };
  const span = trace.getActiveSpan()?.spanContext();
  console.log(
    JSON.stringify({
      timestamp: new Date().toISOString(),
      body,
      severity,
      "service.name": process.env.OTEL_SERVICE_NAME ?? "notification-worker",
      trace_id: span?.traceId ?? "",
      span_id: span?.spanId ?? "",
      trace_flags: span?.traceFlags.toString(16).padStart(2, "0") ?? "00",
      "log.record_id": randomUUID(),
      ...fallback,
      ...fields,
    }),
  );
}
