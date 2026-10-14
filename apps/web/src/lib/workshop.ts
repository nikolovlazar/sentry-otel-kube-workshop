import { randomUUID } from "node:crypto";
import * as Sentry from "@sentry/nextjs";
import {
  context,
  propagation,
  SpanKind,
  SpanStatusCode,
} from "@opentelemetry/api";
import type { ServiceError } from "@grpc/grpc-js";
import { callRpc, tracer, workshopAttributesKey } from "./grpc";
import { log } from "./log";
import type { Run, Scenario, WorkerMode } from "./types";
import { withWorkflowBaggage } from "./workflow-context";
interface Product {
  productId: string;
  name: string;
  unitPriceCents: number;
  cached: boolean;
}
class OriginFailure extends Error {
  constructor(
    public errorCode: string,
    public errorEventId: string,
    public failureService: string,
  ) {
    super(errorCode);
  }
}
async function product(run: Run, cachePolicy = "default"): Promise<Product> {
  return tracer.startActiveSpan(
    "catalog.get_product",
    {
      kind: SpanKind.CLIENT,
      attributes: {
        "http.request.method": "GET",
        "workshop.run_id": run.runId,
        "workshop.scenario": run.scenario,
        "workshop.cache_policy": cachePolicy,
        "workshop.session_id": run.sessionId,
        "workshop.step": "catalog.product",
      },
    },
    async (span) => {
      const headers: Record<string, string> = {
        "x-workshop-session-id": run.sessionId,
      };
      if (process.env.WORKSHOP_BREAK_CATALOG_PROPAGATION === "true")
        log(
          "Catalog trace propagation deliberately disabled for attendee lab",
          "WARN",
          {
            "workshop.run_id": run.runId,
            "workshop.scenario": run.scenario,
            "workshop.session_id": run.sessionId,
            "workshop.step": "catalog.propagation_disabled",
          },
        );
      else propagation.inject(context.active(), headers);
      const url = new URL(
        "/v1/products/demo-widget",
        process.env.CATALOG_URL ?? "http://localhost:8000",
      );
      span.setAttributes({
        "server.address": url.hostname,
        "server.port": Number(url.port || 80),
      });
      url.searchParams.set("run_id", run.runId);
      url.searchParams.set("scenario", run.scenario);
      url.searchParams.set("cache_policy", cachePolicy);
      try {
        const response = await fetch(url, {
          headers,
          cache: "no-store",
          signal: AbortSignal.timeout(3000),
        });
        span.setAttribute("http.response.status_code", response.status);
        const result = await response.json();
        if (!response.ok)
          throw new OriginFailure(
            result.errorCode ?? "CATALOG_UNAVAILABLE",
            result.errorEventId ?? "",
            result.failureService ?? "catalog",
          );
        span.setAttribute("workshop.cache_hit", result.cached);
        log(
          "Catalog response received",
          "INFO",
          {
            "workshop.run_id": run.runId,
            "workshop.scenario": run.scenario,
            "workshop.cache_hit": Boolean(result.cached),
            "workshop.session_id": run.sessionId,
            "workshop.step": "catalog.product",
          },
        );
        return result;
      } catch (error) {
        span.setStatus({ code: SpanStatusCode.ERROR });
        throw error;
      } finally {
        span.end();
      }
    },
  );
}
export async function startRun(input: {
  scenario: Scenario;
  workerMode: WorkerMode;
  sessionId: string;
}): Promise<Run> {
  const runId = randomUUID();
  const workflowContext = withWorkflowBaggage(context.active(), {
    "workshop.run_id": runId,
    "workshop.scenario": input.scenario,
    "workshop.session_id": input.sessionId,
  });
  return tracer.startActiveSpan(
    `workshop.run ${input.scenario}`,
    {},
    workflowContext,
    async (span) => {
      span.setAttributes({
        "workshop.run_id": runId,
        "workshop.scenario": input.scenario,
        "workshop.session_id": input.sessionId,
      });
      const attrs = {
        "workshop.run_id": runId,
        "workshop.scenario": input.scenario,
        "workshop.session_id": input.sessionId,
        "workshop.worker_mode": input.workerMode,
      };
      span.setAttribute("workshop.step", "workflow.run");
      return context.with(
        context.active().setValue(workshopAttributesKey, attrs),
        async () => {
          let run: Run | undefined;
          try {
            run = await callRpc<Run>("BeginRun", {
              runId,
              sessionId: input.sessionId,
              scenario: input.scenario,
              workerMode: input.workerMode,
              traceId: span.spanContext().traceId,
            });
            log("Workshop workflow started", "INFO", {
              "workshop.run_id": runId,
              "workshop.scenario": input.scenario,
              "workshop.session_id": input.sessionId,
              "workshop.step": "workflow.started",
            });
            if (input.scenario === "browser-error") return run;
            if (input.scenario === "next-error")
              throw new Error(
                "Deliberate Next.js workflow failure: checkout summary cannot render",
              );
            const item = await product(
              run,
              input.scenario === "cache-miss-hit"
                ? "fresh"
                : ["slow-postgres", "postgres-lock-timeout", "python-error"].includes(input.scenario)
                  ? "bypass"
                  : "default",
            );
            if (input.scenario === "cache-miss-hit")
              await product(run, "default");
            run = await callRpc<Run>("SubmitOrder", {
              runId,
              productId: item.productId,
              quantity: 1,
              unitPriceCents: item.unitPriceCents,
            });
            log("Order handed to fulfillment", "INFO", {
              "workshop.run_id": runId,
              "workshop.scenario": input.scenario,
              "workshop.status": run.status,
              "workshop.session_id": input.sessionId,
              "workshop.step": "workflow.queued",
            });
            return run;
          } catch (error) {
            span.setStatus({ code: SpanStatusCode.ERROR });
            if (!run) {
              log("Could not create durable workflow state", "ERROR", {
                "workshop.run_id": runId,
                "workshop.scenario": input.scenario,
                "workshop.step": "workflow.begin_failed",
                "workshop.session_id": input.sessionId,
              });
              throw error;
            }
            let origin: OriginFailure;
            if (error instanceof OriginFailure) origin = error;
            else {
              const grpcError = error as ServiceError;
              const eventId = grpcError.metadata
                ?.get("sentry-event-id")?.[0]
                ?.toString();
              const code = grpcError.metadata
                ?.get("workshop-error-code")?.[0]
                ?.toString();
              if (eventId || code)
                origin = new OriginFailure(
                  code ?? "FULFILLMENT_FAILURE",
                  eventId ?? "",
                  "fulfillment",
                );
              else {
                const errorEventId = Sentry.withScope((scope) => {
                  scope.setTags({
                    "workshop.run_id": runId,
                    "workshop.scenario": input.scenario,
                    "workshop.session_id": input.sessionId,
                  });
                  return Sentry.captureException(error);
                });
                origin = new OriginFailure(
                  input.scenario === "next-error"
                    ? "NEXT_DEMO_ERROR"
                    : "WORKFLOW_DEPENDENCY_FAILURE",
                  errorEventId,
                  "web",
                );
              }
            }
            log("Workflow failed at its origin", "ERROR", {
              "workshop.run_id": runId,
              "workshop.scenario": input.scenario,
              "workshop.error_code": origin.errorCode,
              "workshop.error_event_id": origin.errorEventId,
              "workshop.failure_service": origin.failureService,
              "workshop.session_id": input.sessionId,
              "workshop.step": "workflow.failed",
            });
            return await callRpc("RecordFailure", {
              runId,
              failureService: origin.failureService,
              errorCode: origin.errorCode,
              errorEventId: origin.errorEventId,
            });
          } finally {
            span.end();
          }
        },
      );
    },
  );
}
