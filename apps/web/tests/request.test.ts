import { test } from "node:test";
import assert from "node:assert/strict";
import {
  parseStartRequest,
  validRunId,
  terminalStatuses,
} from "../src/lib/types";
test("scenario contract rejects invalid inputs", () => {
  assert.throws(() => parseStartRequest({ scenario: "unknown" }));
  assert.throws(() =>
    parseStartRequest({ scenario: "async-worker", workerMode: "forever" }),
  );
  assert.throws(() =>
    parseStartRequest({ scenario: "next-error", sessionId: "../other" }),
  );
});
test("worker modes and session defaults are stable", () => {
  assert.equal(
    parseStartRequest({ scenario: "full-journey", sessionId: "demo-session" })
      .workerMode,
    "success",
  );
  assert.deepEqual(
    parseStartRequest({
      scenario: "async-worker",
      workerMode: "recover",
      sessionId: "demo",
    }),
    { scenario: "async-worker", workerMode: "recover", sessionId: "demo" },
  );
});
test("only UUIDv4 run IDs accepted and canonical statuses stop polling", () => {
  assert.equal(validRunId("458ab88a-e90e-4c15-86ca-1f4d0617c360"), true);
  assert.equal(validRunId("demo"), false);
  assert.equal(terminalStatuses.has("completed"), true);
  assert.equal(terminalStatuses.has("dead_lettered"), true);
  assert.equal(terminalStatuses.has("retrying"), false);
});
