import * as grpc from "@grpc/grpc-js";
import * as protoLoader from "@grpc/proto-loader";
import {
  context,
  createContextKey,
  propagation,
  trace,
  SpanKind,
  SpanStatusCode,
} from "@opentelemetry/api";
import path from "node:path";
import type { Run } from "./types";
const definition = protoLoader.loadSync(
  process.env.WORKSHOP_PROTO_PATH ??
    path.resolve(process.cwd(), "../../proto/workshop.proto"),
  {
    keepCase: false,
    longs: Number,
    enums: String,
    defaults: true,
    oneofs: true,
  },
);
const descriptor = grpc.loadPackageDefinition(definition) as unknown as {
  workshop: { v1: { Fulfillment: grpc.ServiceClientConstructor } };
};
const client = new descriptor.workshop.v1.Fulfillment(
  process.env.FULFILLMENT_ADDR ?? "localhost:50051",
  grpc.credentials.createInsecure(),
);
export const workshopAttributesKey = createContextKey(
  "workshop.run.attributes",
);
export const tracer = trace.getTracer("workshop.web");
export function callRpc<T = Run>(method: string, request: object): Promise<T> {
  return tracer.startActiveSpan(
    `workshop.v1.Fulfillment/${method}`,
    {
      kind: SpanKind.CLIENT,
      attributes: {
        ...((context.active().getValue(workshopAttributesKey) as
          | Record<string, string | number | boolean>
          | undefined) ?? {}),
        "workshop.step": `grpc.${method}`,
        "server.address": (
          process.env.FULFILLMENT_ADDR ?? "localhost:50051"
        ).split(":")[0],
        "server.port": Number(
          (process.env.FULFILLMENT_ADDR ?? "localhost:50051").split(":")[1],
        ),
        "rpc.system": "grpc",
        "rpc.service": "workshop.v1.Fulfillment",
        "rpc.method": method,
        ...("runId" in request
          ? { "workshop.run_id": String(request.runId) }
          : {}),
        ...("scenario" in request
          ? { "workshop.scenario": String(request.scenario) }
          : {}),
        ...("sessionId" in request
          ? { "workshop.session_id": String(request.sessionId) }
          : {}),
      },
    },
    async (span) => {
      const carrier: Record<string, string> = {};
      propagation.inject(context.active(), carrier);
      const metadata = new grpc.Metadata();
      for (const [key, value] of Object.entries(carrier))
        metadata.set(key, value);
      try {
        const response = await new Promise<T>((resolve, reject) => {
          (client as unknown as Record<string, Function>)[method](
            request,
            metadata,
            { deadline: Date.now() + 3000 },
            (error: grpc.ServiceError | null, response: T) =>
              error ? reject(error) : resolve(response),
          );
        });
        const result = response as Run | undefined;
        if (result?.runId)
          span.setAttributes({
            "workshop.run_id": result.runId,
            "workshop.scenario": result.scenario,
            "workshop.session_id": result.sessionId,
            "workshop.worker_mode": result.workerMode,
          });
        span.setAttribute("rpc.grpc.status_code", 0);
        return response;
      } catch (error) {
        span.setAttribute(
          "rpc.grpc.status_code",
          (error as grpc.ServiceError).code ?? grpc.status.UNKNOWN,
        );
        span.setStatus({
          code: SpanStatusCode.ERROR,
          message: error instanceof Error ? error.message : "gRPC failed",
        });
        throw error;
      } finally {
        span.end();
      }
    },
  );
}
export function ready(): Promise<void> {
  return new Promise((resolve, reject) =>
    client.waitForReady(Date.now() + 2500, (error) =>
      error ? reject(error) : resolve(),
    ),
  );
}
