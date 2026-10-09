import { SpanKind } from "@opentelemetry/api";
import {
  AlwaysOnSampler,
  ParentBasedSampler,
  SamplingDecision,
  type Sampler,
  type SamplingResult,
} from "@opentelemetry/sdk-trace-base";

/** Drop the whole health request, including Next.js framework descendants. */
export class HealthCheckSampler implements Sampler {
  private readonly workflows = new ParentBasedSampler({
    root: new AlwaysOnSampler(),
    // Preserve 100% workshop sampling even for an unsampled incoming carrier.
    remoteParentNotSampled: new AlwaysOnSampler(),
  });

  shouldSample(...args: Parameters<Sampler["shouldSample"]>): SamplingResult {
    const target = args[4]["http.target"] ?? args[4]["url.path"];
    if (
      args[3] === SpanKind.SERVER &&
      typeof target === "string" &&
      /^\/health\/(?:live|ready)\/?(?:\?|$)/.test(target)
    ) {
      return { decision: SamplingDecision.NOT_RECORD };
    }
    // Locally unsampled parents keep every child of a dropped probe quiet.
    return this.workflows.shouldSample(...args);
  }

  toString(): string {
    return "HealthCheckSampler";
  }
}
