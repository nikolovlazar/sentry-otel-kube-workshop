import { randomUUID } from "node:crypto";
import http from "node:http";
import { createClient } from "redis";
import * as Sentry from "@sentry/node";
import {
  context,
  propagation,
  trace,
  ROOT_CONTEXT,
  SpanKind,
  SpanStatusCode,
} from "@opentelemetry/api";
import {
  callRpc,
  closeGrpc,
  ready,
  tracer,
  workshopAttributesKey,
} from "./grpc.js";
import {
  deadLetterLua,
  failsAtAttempt,
  finalEventId,
  keys,
  parseEntries,
  retryDelay,
  terminalStatuses,
  type Message,
  type PendingError,
} from "./contracts.js";
import { log } from "./log.js";
import {
  beginErrorDelivery,
  errorWasDelivered,
  flushTelemetry,
} from "./telemetry.js";
const redis = createClient({
  url: process.env.REDIS_URL ?? "redis://localhost:6379",
});
const blocking = redis.duplicate();
const consumer = process.env.POD_UID || `worker-${randomUUID()}`;
let stopping = false;
let started = false;
let processing: Promise<void> | undefined;
let errorDrain: Promise<void> | undefined;
const pause = (duration: number) =>
  new Promise((resolve) => setTimeout(resolve, duration));
for (const connection of [redis, blocking])
  connection.on("error", (error) =>
    log("Redis connection error", "ERROR", {
      "workshop.step": "worker.redis_connection",
      "error.message": error.message,
    }),
  );
async function command(
  name: string,
  args: string[],
  fields: Record<string, string | number> = {},
): Promise<unknown> {
  return tracer.startActiveSpan(
    `redis.${name}`,
    {
      kind: SpanKind.CLIENT,
      attributes: {
        ...((context.active().getValue(workshopAttributesKey) as
          | Record<string, string | number | boolean>
          | undefined) ?? {}),
        "workshop.step": `redis.${name}`,
        "db.system.name": "redis",
        "db.operation.name": name,
        "server.address": new URL(
          process.env.REDIS_URL ?? "redis://localhost:6379",
        ).hostname,
        ...fields,
      },
    },
    async (span) => {
      try {
        return await redis.sendCommand(args);
      } catch (error) {
        span.setStatus({ code: SpanStatusCode.ERROR });
        throw error;
      } finally {
        span.end();
      }
    },
  );
}
async function acknowledge(message: Message) {
  await command("XACK", ["XACK", keys.stream, keys.group, message.id], {
    "workshop.run_id": message.envelope.run_id,
  });
}
async function deadLetter(message: Message, eventId: string, error?: Error) {
  const e = message.envelope;
  const carrier: Record<string, string> = {};
  propagation.inject(context.active(), carrier);
  const pending: PendingError = {
    eventId,
    message:
      error?.message ??
      "Deliberate notification failure: all three delivery attempts exhausted",
    name: error?.name ?? "Error",
    stack: error?.stack ?? "",
    envelope: e,
    carrier,
    traceId: trace.getActiveSpan()?.spanContext().traceId ?? "",
    spanId: trace.getActiveSpan()?.spanContext().spanId ?? "",
    messageId: message.id,
  };
  return (
    Number(
      await command(
        "EVAL",
        [
          "EVAL",
          deadLetterLua,
          "3",
          keys.deadLetters,
          keys.deadLettered,
          keys.errorOutbox,
          e.run_id,
          eventId,
          message.id,
          e.session_id,
          e.scenario,
          e.worker_mode,
          e.traceparent,
          e.tracestate,
          e.baggage,
          JSON.stringify(pending),
        ],
        { "workshop.run_id": e.run_id },
      ),
    ) === 1
  );
}
async function drainErrors() {
  let cursor = "0";
  while (!stopping) {
    try {
      const pending = await redis.sendCommand([
        "HSCAN",
        keys.errorOutbox,
        cursor,
        "COUNT",
        "20",
      ]);
      cursor = Array.isArray(pending) ? String(pending[0]) : "0";
      const fields =
        Array.isArray(pending) && Array.isArray(pending[1]) ? pending[1] : [];
      for (let index = 0; index < fields.length && !stopping; index += 2) {
        const runId = String(fields[index]);
        const entry = JSON.parse(String(fields[index + 1])) as PendingError;
        const origin = propagation
          .extract(ROOT_CONTEXT, entry.carrier)
          .setValue(workshopAttributesKey, {
            "workshop.run_id": runId,
            "workshop.scenario": entry.envelope.scenario,
            "workshop.session_id": entry.envelope.session_id,
          });
        // Use the originating consumer's stored context, rather than attributing the error to the drain.
        await context.with(origin, async () => {
          const error = new Error(entry.message);
          error.name = entry.name;
          if (entry.stack) error.stack = entry.stack;
          beginErrorDelivery(entry.eventId);
          Sentry.withScope((scope) => {
            scope.setTags({
              "workshop.run_id": runId,
              "workshop.scenario": entry.envelope.scenario,
              "workshop.session_id": entry.envelope.session_id,
              "workshop.step": "notification.dead_letter",
            });
            Sentry.captureException(error, { event_id: entry.eventId });
          });
          // flush alone only proves buffer drainage. Also require a successful HTTP response.
          if ((await Sentry.flush(2000)) && errorWasDelivered(entry.eventId)) {
            await command("HDEL", ["HDEL", keys.errorOutbox, runId], {
              "workshop.run_id": runId,
            });
            beginErrorDelivery(entry.eventId);
            log("Final error submitted from durable outbox", "INFO", {
              "workshop.run_id": runId,
              "workshop.scenario": entry.envelope.scenario,
              "workshop.session_id": entry.envelope.session_id,
              "workshop.step": "notification.error_submitted",
              "workshop.error_event_id": entry.eventId,
            });
          } else
            log(
              "Final error delivery pending; business state is already terminal",
              "WARN",
              {
                "workshop.run_id": runId,
                "workshop.step": "notification.error_pending",
                "workshop.scenario": entry.envelope.scenario,
                "workshop.session_id": entry.envelope.session_id,
                "workshop.error_event_id": entry.eventId,
              },
            );
        });
      }
    } catch (error) {
      if (!stopping)
        log("Error outbox will retry delivery", "WARN", {
          "workshop.step": "notification.error_outbox_retry",
          "error.message":
            error instanceof Error ? error.message : String(error),
        });
    }
    if (!stopping) await pause(1000);
  }
}

async function processMessage(message: Message) {
  const envelope = message.envelope;
  const attributes = {
    "workshop.run_id": envelope.run_id,
    "workshop.scenario": envelope.scenario,
    "workshop.session_id": envelope.session_id,
    "workshop.worker_mode": envelope.worker_mode,
    "messaging.system": "redis",
    "messaging.destination.name": keys.stream,
    "messaging.operation.type": "process",
    "messaging.message.id": message.id,
  };
  const origin = propagation
    .extract(ROOT_CONTEXT, envelope)
    .setValue(workshopAttributesKey, {
      "workshop.run_id": envelope.run_id,
      "workshop.scenario": envelope.scenario,
      "workshop.session_id": envelope.session_id,
      "workshop.worker_mode": envelope.worker_mode,
    });
  return context.with(origin, async () => {
    // Continue the producer's context afresh on each attempt. Retry spans are siblings in one trace.
    let current = await callRpc("GetRun", { runId: envelope.run_id });
    if (terminalStatuses.has(current.status)) {
      if (current.status === "dead_lettered")
        await deadLetter(message, current.errorEventId);
      await acknowledge(message);
      return;
    }
    while (!stopping) {
      const stored =
        Number(
          await command("HGET", ["HGET", keys.attempts, message.id], {
            "workshop.run_id": envelope.run_id,
          }),
        ) || 0;
      const attempt =
        stored >= 3
          ? 3
          : Number(
              await command(
                "HINCRBY",
                ["HINCRBY", keys.attempts, message.id, "1"],
                { "workshop.run_id": envelope.run_id },
              ),
            );
      const outcome = await tracer.startActiveSpan(
        "notification.process",
        {
          kind: SpanKind.CONSUMER,
          attributes: { ...attributes, "workshop.attempt": attempt },
        },
        origin,
        async (span) => {
          try {
            log("Notification attempt started", "INFO", {
              ...attributes,
              "workshop.attempt": attempt,
              "workshop.step": "notification.attempt",
            });
            // A controlled notification provider, with deterministic failures instead of external credentials.
            await pause(75);
            if (failsAtAttempt(envelope.worker_mode, attempt)) {
              span.setStatus({
                code: SpanStatusCode.ERROR,
                message: "Deliberate notification provider failure",
              });
              if (attempt < 3) {
                await callRpc("RecordNotificationResult", {
                  runId: envelope.run_id,
                  messageId: message.id,
                  attempt,
                  outcome: "retry",
                  errorCode: "NOTIFICATION_RETRY",
                });
                log("Notification failed; retry scheduled", "WARN", {
                  ...attributes,
                  "workshop.attempt": attempt,
                  "workshop.step": "notification.retry",
                  "workshop.retry_delay_ms": retryDelay(attempt),
                });
                return "retry";
              }
              const eventId = finalEventId(envelope.run_id);
              // Deterministic event IDs also deduplicate transport replay at Sentry ingestion.
              const error = new Error(
                "Deliberate notification failure: all three delivery attempts exhausted",
              );
              await deadLetter(message, eventId, error);
              await callRpc("RecordNotificationResult", {
                runId: envelope.run_id,
                messageId: message.id,
                attempt,
                outcome: "dead_lettered",
                errorCode: "NOTIFICATION_EXHAUSTED",
                errorEventId: eventId,
              });
              log(
                "Notification attempts exhausted; message dead lettered",
                "ERROR",
                {
                  ...attributes,
                  "workshop.attempt": attempt,
                  "workshop.step": "notification.dead_letter",
                  "workshop.error_event_id": eventId,
                  "workshop.error_code": "NOTIFICATION_EXHAUSTED",
                },
              );
            } else {
              await callRpc("RecordNotificationResult", {
                runId: envelope.run_id,
                messageId: message.id,
                attempt,
                outcome: "succeeded",
              });
              log("Notification delivered", "INFO", {
                ...attributes,
                "workshop.attempt": attempt,
                "workshop.step": "notification.succeeded",
              });
            }
            await acknowledge(message);
            log("Notification message acknowledged", "INFO", {
              ...attributes,
              "workshop.attempt": attempt,
              "workshop.step": "notification.ack",
            });
            return "done";
          } catch (error) {
            span.setStatus({ code: SpanStatusCode.ERROR });
            log("Worker dependency failed; message remains pending", "ERROR", {
              ...attributes,
              "workshop.attempt": attempt,
              "workshop.step": "notification.dependency_failed",
              "error.message":
                error instanceof Error ? error.message : String(error),
            });
            throw error;
          } finally {
            span.end();
          }
        },
      );
      if (outcome === "done") return;
      await pause(retryDelay(attempt));
    }
  });
}
const health = http.createServer(async (request, response) => {
  if (request.url === "/health/live") {
    response.writeHead(200, { "content-type": "application/json" });
    response.end(
      JSON.stringify({ status: "ok", service: "notification-worker" }),
    );
    return;
  }
  if (request.url === "/health/ready") {
    try {
      if (stopping || !started || !redis.isReady || !blocking.isReady)
        throw new Error("Worker is not ready");
      await ready();
      response.writeHead(200, { "content-type": "application/json" });
      response.end(JSON.stringify({ status: "ready" }));
    } catch {
      response.writeHead(503, { "content-type": "application/json" });
      response.end(JSON.stringify({ status: "not-ready" }));
    }
    return;
  }
  response.writeHead(404);
  response.end();
});
health.listen(Number(process.env.PORT ?? 8080), "0.0.0.0");
async function run() {
  await Promise.all([redis.connect(), blocking.connect()]);
  try {
    await redis.sendCommand([
      "XGROUP",
      "CREATE",
      keys.stream,
      keys.group,
      "0",
      "MKSTREAM",
    ]);
  } catch (error) {
    if (!(error instanceof Error) || !error.message.includes("BUSYGROUP"))
      throw error;
  }
  started = true;
  errorDrain = drainErrors();
  log("Notification worker ready", "INFO", {
    "workshop.step": "worker.ready",
    "workshop.consumer": consumer,
  });
  let lastClaim = 0;
  while (!stopping) {
    try {
      let messages: Message[] = [];
      if (Date.now() - lastClaim >= 2000) {
        lastClaim = Date.now();
        const claimed = await redis.sendCommand([
          "XAUTOCLAIM",
          keys.stream,
          keys.group,
          consumer,
          "2000",
          "0-0",
          "COUNT",
          "1",
        ]);
        if (Array.isArray(claimed)) messages = parseEntries(claimed[1]);
      }
      if (!messages.length) {
        const result = await blocking.sendCommand([
          "XREADGROUP",
          "GROUP",
          keys.group,
          consumer,
          "COUNT",
          "1",
          "BLOCK",
          "1000",
          "STREAMS",
          keys.stream,
          ">",
        ]);
        if (Array.isArray(result) && Array.isArray(result[0]))
          messages = parseEntries(result[0][1]);
      }
      for (const message of messages) {
        if (stopping) break;
        processing = processMessage(message);
        await processing;
        processing = undefined;
      }
    } catch (error) {
      processing = undefined;
      if (!stopping) {
        log(
          "Queue processing interrupted; pending work will be reclaimed",
          "ERROR",
          {
            "workshop.step": "worker.reclaim",
            "error.message":
              error instanceof Error ? error.message : String(error),
          },
        );
        await pause(500);
      }
    }
  }
}
async function shutdown() {
  if (stopping) return;
  stopping = true;
  started = false;
  try {
    blocking.destroy();
  } catch {}
  await processing?.catch(() => {});
  await errorDrain?.catch(() => {});
  await new Promise<void>((resolve) => health.close(() => resolve()));
  if (redis.isOpen) await redis.quit().catch(() => redis.destroy());
  closeGrpc();
  await flushTelemetry();
}
process.once("SIGTERM", () => void shutdown());
process.once("SIGINT", () => void shutdown());
run().catch(async (error) => {
  log("Worker startup failed", "ERROR", {
    "workshop.step": "worker.startup_failed",
    "error.message": error instanceof Error ? error.message : String(error),
  });
  Sentry.captureException(error);
  await shutdown();
  process.exitCode = 1;
});
