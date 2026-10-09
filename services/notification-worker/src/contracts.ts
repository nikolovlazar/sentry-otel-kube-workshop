import { createHash } from "node:crypto";
export const keys = {
  stream: "workshop:notifications",
  group: "notification-workers",
  attempts: "workshop:attempts",
  deadLetters: "workshop:dead-letters",
  deadLettered: "workshop:dead-lettered",
  errorOutbox: "workshop:error-outbox",
} as const;
export type WorkerMode = "success" | "recover" | "exhaust";
export interface Envelope {
  run_id: string;
  session_id: string;
  scenario: string;
  worker_mode: WorkerMode;
  traceparent: string;
  tracestate: string;
  baggage: string;
}
export interface Message {
  id: string;
  envelope: Envelope;
}
export interface PendingError {
  eventId: string;
  message: string;
  name: string;
  stack: string;
  envelope: Envelope;
  carrier: Record<string, string>;
  traceId: string;
  spanId: string;
  messageId: string;
}
export interface Run {
  runId: string;
  sessionId: string;
  scenario: string;
  workerMode: WorkerMode;
  status: string;
  traceId: string;
  attempts: number;
  failureService: string;
  errorCode: string;
  errorEventId: string;
  createdAt: string;
  updatedAt: string;
}
export const terminalStatuses = new Set([
  "completed",
  "failed",
  "dead_lettered",
]);
export const retryDelay = (attempt: number) => (attempt === 1 ? 250 : 500);
export function failsAtAttempt(mode: WorkerMode, attempt: number) {
  return mode === "exhaust" || (mode === "recover" && attempt < 3);
}
export function finalEventId(runId: string) {
  return createHash("sha256")
    .update(`workshop.notification.exhausted:${runId}`)
    .digest("hex")
    .slice(0, 32);
}
export function parseEntries(value: unknown): Message[] {
  if (!Array.isArray(value)) return [];
  return value.map((entry) => {
    if (
      !Array.isArray(entry) ||
      typeof entry[0] !== "string" ||
      !Array.isArray(entry[1])
    )
      throw new Error("Invalid Redis Stream entry.");
    const fields: Record<string, string> = {};
    for (let index = 0; index < entry[1].length; index += 2)
      fields[String(entry[1][index])] = String(entry[1][index + 1]);
    if (
      !/^[0-9a-f-]{36}$/i.test(fields.run_id ?? "") ||
      !fields.session_id ||
      !["success", "recover", "exhaust"].includes(fields.worker_mode)
    )
      throw new Error("Invalid notification envelope.");
    return {
      id: entry[0],
      envelope: {
        ...fields,
        traceparent: fields.traceparent ?? "",
        tracestate: fields.tracestate ?? "",
        baggage: fields.baggage ?? "",
      } as unknown as Envelope,
    };
  });
}
// The dedup hash, DLQ append, and error outbox handoff happen together. Replaying a pending message cannot append twice.
export const deadLetterLua = `if redis.call('HEXISTS', KEYS[2], ARGV[1]) == 1 then return 0 end
redis.call('HSET', KEYS[2], ARGV[1], ARGV[2])
redis.call('HSET', KEYS[3], ARGV[1], ARGV[10])
redis.call('XADD', KEYS[1], '*', 'run_id', ARGV[1], 'error_event_id', ARGV[2], 'source_message_id', ARGV[3], 'session_id', ARGV[4], 'scenario', ARGV[5], 'worker_mode', ARGV[6], 'traceparent', ARGV[7], 'tracestate', ARGV[8], 'baggage', ARGV[9], 'attempts', '3')
return 1`;
