export const scenarios = [
  "full-journey",
  "cache-miss-hit",
  "slow-postgres",
  "postgres-lock-timeout",
  "python-error",
  "go-error",
  "async-worker",
  "next-error",
  "browser-error",
] as const;
export type Scenario = (typeof scenarios)[number];
export type WorkerMode = "success" | "recover" | "exhaust";
export interface Run {
  runId: string;
  sessionId: string;
  scenario: Scenario;
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
export interface PublicConfig {
  dsn: string;
  orgSlug: string;
  baseUrl: string;
  release: string;
  environment: string;
  sessionId: string;
}
export const terminalStatuses = new Set([
  "completed",
  "failed",
  "dead_lettered",
]);
export function validRunId(value: unknown): value is string {
  return (
    typeof value === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
      value,
    )
  );
}
export function parseStartRequest(value: unknown): {
  scenario: Scenario;
  workerMode: WorkerMode;
  sessionId: string;
} {
  if (!value || typeof value !== "object")
    throw new Error("Expected a JSON object.");
  const input = value as Record<string, unknown>;
  if (!scenarios.includes(input.scenario as Scenario))
    throw new Error("Choose a supported scenario.");
  const workerMode = input.workerMode ?? "success";
  if (!["success", "recover", "exhaust"].includes(workerMode as string))
    throw new Error("Choose a supported worker mode.");
  const sessionId =
    input.sessionId ?? process.env.WORKSHOP_SESSION_ID ?? "local-workshop";
  if (typeof sessionId !== "string" || !/^[a-zA-Z0-9_-]{1,64}$/.test(sessionId))
    throw new Error("Invalid session ID.");
  return {
    scenario: input.scenario as Scenario,
    workerMode: workerMode as WorkerMode,
    sessionId,
  };
}
