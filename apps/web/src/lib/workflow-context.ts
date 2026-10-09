import { propagation, type Context } from "@opentelemetry/api";
import type {
  Span,
  ReadableSpan,
  SpanProcessor,
} from "@opentelemetry/sdk-trace-base";

export const workflowBaggageKeys = [
  "workshop.run_id",
  "workshop.session_id",
  "workshop.scenario",
] as const;
export function baggageAttributes(
  parentContext: Context,
): Record<string, string> {
  const baggage = propagation.getBaggage(parentContext);
  const attributes: Record<string, string> = {};
  for (const key of workflowBaggageKeys) {
    const value = baggage?.getEntry(key)?.value;
    if (value) attributes[key] = value;
  }
  return attributes;
}
export function withWorkflowBaggage(
  parentContext: Context,
  attributes: Record<string, string>,
): Context {
  let baggage =
    propagation.getBaggage(parentContext) ?? propagation.createBaggage({});
  for (const key of workflowBaggageKeys) {
    if (attributes[key])
      baggage = baggage.setEntry(key, { value: attributes[key] });
  }
  return propagation.setBaggage(parentContext, baggage);
}
// Span attributes are local; baggage transports the workflow identity across every hop.
export class WorkflowAttributesProcessor implements SpanProcessor {
  onStart(span: Span, parentContext: Context): void {
    span.setAttributes({
      ...baggageAttributes(parentContext),
      "workshop.step": span.name,
    });
  }
  onEnd(_span: ReadableSpan): void {}
  forceFlush(): Promise<void> {
    return Promise.resolve();
  }
  shutdown(): Promise<void> {
    return Promise.resolve();
  }
}
