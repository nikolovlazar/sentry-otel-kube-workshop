import test from "node:test";
import assert from "node:assert/strict";
import { propagation, trace, ROOT_CONTEXT, defaultTextMapGetter, defaultTextMapSetter } from "@opentelemetry/api";
import { CompositePropagator, W3CBaggagePropagator, W3CTraceContextPropagator } from "@opentelemetry/core";
import { BasicTracerProvider, InMemorySpanExporter, SimpleSpanProcessor } from "@opentelemetry/sdk-trace-base";
import { baggageAttributes, withWorkflowBaggage, WorkflowAttributesProcessor } from "../src/workflow-context.js";

test("wire baggage enriches downstream library spans without a global run map", async () => {
  const exporter=new InMemorySpanExporter();
  const provider=new BasicTracerProvider({spanProcessors:[new WorkflowAttributesProcessor(),new SimpleSpanProcessor(exporter)]});
  const tracer=provider.getTracer("workshop.test");
  const propagator=new CompositePropagator({propagators:[new W3CTraceContextPropagator(),new W3CBaggagePropagator()]});
  const identity={"workshop.run_id":"bf02a8a7-f859-4d29-a13f-957ee09447c5","workshop.session_id":"baggage-test","workshop.scenario":"async-worker"};
  const initial=propagation.setBaggage(ROOT_CONTEXT,propagation.createBaggage({"sentry-environment":{value:"workshop-local"}}));
  const sourceContext=withWorkflowBaggage(initial,identity);
  const producer=tracer.startSpan("order.enqueue",{},sourceContext);
  const carrier:Record<string,string>={};
  propagator.inject(trace.setSpan(sourceContext,producer),carrier,defaultTextMapSetter);
  const received=propagator.extract(ROOT_CONTEXT,carrier,defaultTextMapGetter);
  assert.deepEqual(baggageAttributes(received),identity);
  assert.equal(propagation.getBaggage(received)?.getEntry("sentry-environment")?.value,"workshop-local");
  const librarySpan=tracer.startSpan("redis.XACK",{},received);
  assert.equal(librarySpan.spanContext().traceId,producer.spanContext().traceId);
  librarySpan.end();producer.end();await provider.forceFlush();
  const downstream=exporter.getFinishedSpans().find(span=>span.name==="redis.XACK")!;
  for(const [key,value] of Object.entries(identity))assert.equal(downstream.attributes[key],value);
  assert.equal(downstream.attributes["workshop.step"],"redis.XACK");
  await provider.shutdown();
});
