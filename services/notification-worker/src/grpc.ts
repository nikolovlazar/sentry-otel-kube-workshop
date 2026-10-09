import * as grpc from "@grpc/grpc-js";
import * as protoLoader from "@grpc/proto-loader";
import path from "node:path";
import {
  context,
  createContextKey,
  propagation,
  trace,
  SpanKind,
  SpanStatusCode,
} from "@opentelemetry/api";
import type { Run } from "./contracts.js";
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
export const tracer = trace.getTracer("workshop.notification-worker");
export function callRpc(method: string, request: object): Promise<Run> {
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
      },
    },
    async (span) => {
      const carrier: Record<string, string> = {};
      propagation.inject(context.active(), carrier);
      const metadata = new grpc.Metadata();
      for (const [key, value] of Object.entries(carrier))
        metadata.set(key, value);
      try {
        const response = await new Promise<Run>((resolve, reject) =>
          (client as unknown as Record<string, Function>)[method](
            request,
            metadata,
            { deadline: Date.now() + 3000 },
            (error: grpc.ServiceError | null, response: Run) =>
              error ? reject(error) : resolve(response),
          ),
        );
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
        span.setStatus({ code: SpanStatusCode.ERROR });
        throw error;
      } finally {
        span.end();
      }
    },
  );
}
export const ready = () =>
  new Promise<void>((resolve, reject) =>
    client.waitForReady(Date.now() + 1000, (error) =>
      error ? reject(error) : resolve(),
    ),
  );
export function closeGrpc() {
  client.close();
}
