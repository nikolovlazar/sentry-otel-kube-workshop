import test from "node:test";
import assert from "node:assert/strict";
import {
  failsAtAttempt,
  finalEventId,
  parseEntries,
  retryDelay,
} from "../src/contracts.js";
test("the delivery matrix has exactly three bounded attempts", () => {
  assert.deepEqual(
    [1, 2, 3].map((attempt) => failsAtAttempt("success", attempt)),
    [false, false, false],
  );
  assert.deepEqual(
    [1, 2, 3].map((attempt) => failsAtAttempt("recover", attempt)),
    [true, true, false],
  );
  assert.deepEqual(
    [1, 2, 3].map((attempt) => failsAtAttempt("exhaust", attempt)),
    [true, true, true],
  );
  assert.equal(retryDelay(1), 250);
  assert.equal(retryDelay(2), 500);
});
test("stream parsing retains the exact producer propagation carrier", () => {
  const carrier = "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01";
  const [message] = parseEntries([
    [
      "123-0",
      [
        "run_id",
        "2da651a0-2722-4b8c-8dbb-ad7b6a3709b0",
        "session_id",
        "test",
        "scenario",
        "async-worker",
        "worker_mode",
        "recover",
        "traceparent",
        carrier,
        "baggage",
        "workshop=demo",
      ],
    ],
  ]);
  assert.equal(message.id, "123-0");
  assert.equal(message.envelope.traceparent, carrier);
  assert.equal(message.envelope.baggage, "workshop=demo");
  assert.throws(
    () => parseEntries([["123-0", ["worker_mode", "random"]]]),
    /Invalid notification/,
  );
});
test("terminal event IDs remain stable across delivery replay and distinct between runs", () => {
  const first = finalEventId("run-a");
  assert.match(first, /^[0-9a-f]{32}$/);
  assert.equal(first, finalEventId("run-a"));
  assert.notEqual(first, finalEventId("run-b"));
});
