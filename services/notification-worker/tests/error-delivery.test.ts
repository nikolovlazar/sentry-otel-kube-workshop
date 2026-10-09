import test from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { once } from "node:events";
import { context, propagation, ROOT_CONTEXT } from "@opentelemetry/api";

test("an error keeps its originating consumer IDs and retries after failed transport", async () => {
  let status = 500;
  const events: Record<string, any>[] = [];
  const server = http.createServer((request, response) => {
    let body = "";
    request.on("data", (chunk) => {
      body += chunk.toString();
    });
    request.on("end", () => {
      if (request.url?.includes("/envelope/")) {
        for (const line of body.split("\n")) {
          try {
            const item = JSON.parse(line);
            if (item.exception) events.push(item);
          } catch {}
        }
      }
      response.writeHead(status);
      response.end("{}");
    });
  });
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  const port = (server.address() as { port: number }).port;
  process.env.SENTRY_DSN = `http://public@127.0.0.1:${port}/1`;
  process.env.OTEL_EXPORTER_OTLP_ENDPOINT = `http://127.0.0.1:${port}`;
  const telemetry = await import("../src/telemetry.js");
  const Sentry = await import("@sentry/node");
  const eventId = "22222222222222222222222222222222";
  const traceId = "1234567890abcdef1234567890abcdef";
  const spanId = "1234567890abcdef";
  const origin = propagation.extract(ROOT_CONTEXT, {
    traceparent: `00-${traceId}-${spanId}-01`,
  });
  const error = new Error("A persisted notification provider exception");
  try {
    async function submit() {
      telemetry.beginErrorDelivery(eventId);
      await context.with(origin, async () => {
        Sentry.captureException(new Error(error.message), {
          event_id: eventId,
        });
        assert.equal(await Sentry.flush(2000), true);
      });
    }
    await submit();
    assert.equal(
      telemetry.errorWasDelivered(eventId),
      false,
      "buffer flush must not count HTTP500 as success",
    );
    status = 200;
    await submit();
    assert.equal(telemetry.errorWasDelivered(eventId), true);
    assert.equal(
      events.length,
      2,
      "SDK deduplication must allow replay of the same logical error",
    );
    assert.equal(events[1].event_id, eventId);
    assert.equal(events[1].contexts.trace.trace_id, traceId);
    assert.equal(events[1].contexts.trace.span_id, spanId);
  } finally {
    await telemetry.flushTelemetry();
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});
