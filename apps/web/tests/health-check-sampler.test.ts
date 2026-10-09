import { test } from "node:test";
import assert from "node:assert/strict";
import { ROOT_CONTEXT, SpanKind, trace, TraceFlags } from "@opentelemetry/api";
import {
  BasicTracerProvider,
  InMemorySpanExporter,
  SimpleSpanProcessor,
} from "@opentelemetry/sdk-trace-base";
import { HealthCheckSampler } from "../src/lib/health-check-sampler";

function pipeline() {
  const exporter = new InMemorySpanExporter();
  const provider = new BasicTracerProvider({
    sampler: new HealthCheckSampler(),
    spanProcessors: [new SimpleSpanProcessor(exporter)],
  });
  return { exporter, provider, tracer: provider.getTracer("test") };
}

test("health requests and framework descendants never export, even under sampled remote parents", async () => {
  for (const path of ["/health/live", "/health/ready", "/health/ready?probe=1", "/health/live/"]) {
    const { exporter, provider, tracer } = pipeline();
    const remote = trace.setSpanContext(ROOT_CONTEXT, {
      traceId: "1".repeat(32), spanId: "2".repeat(16),
      traceFlags: TraceFlags.SAMPLED, isRemote: true,
    });
    const request = tracer.startSpan("GET", { kind: SpanKind.SERVER, attributes: { "http.target": path } }, remote);
    const child = tracer.startSpan("load route module", {}, trace.setSpan(ROOT_CONTEXT, request));
    const grandchild = tracer.startSpan("start response", {}, trace.setSpan(ROOT_CONTEXT, child));
    assert.equal(request.isRecording(), false);
    assert.equal(child.isRecording(), false);
    assert.equal(grandchild.isRecording(), false);
    grandchild.end(); child.end(); request.end();
    await provider.forceFlush();
    assert.equal(exporter.getFinishedSpans().length, 0);
    await provider.shutdown();
  }
});

test("workflow traces retain their parentage and workshop sampling", async () => {
  const { exporter, provider, tracer } = pipeline();
  const remote = trace.setSpanContext(ROOT_CONTEXT, {
    traceId: "3".repeat(32), spanId: "4".repeat(16),
    traceFlags: TraceFlags.NONE, isRemote: true,
  });
  const request = tracer.startSpan("POST", { kind: SpanKind.SERVER, attributes: { "http.target": "/api/runs" } }, remote);
  const child = tracer.startSpan("workshop.run", {}, trace.setSpan(ROOT_CONTEXT, request));
  assert.equal(request.isRecording(), true);
  assert.equal(child.isRecording(), true);
  assert.equal(child.spanContext().traceId, "3".repeat(32));
  child.end(); request.end();
  await provider.forceFlush();
  assert.equal(exporter.getFinishedSpans().length, 2);
  await provider.shutdown();
});

test("similar paths are not accidentally excluded", async () => {
  const { exporter, provider, tracer } = pipeline();
  for (const path of ["/health/readyish", "/api/health/ready", "/health/live/details"]) {
    const span = tracer.startSpan("GET", { kind: SpanKind.SERVER, attributes: { "http.target": path } }, ROOT_CONTEXT);
    assert.equal(span.isRecording(), true);
    span.end();
  }
  await provider.forceFlush();
  assert.equal(exporter.getFinishedSpans().length, 3);
  await provider.shutdown();
});
