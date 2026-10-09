/**
 * Real Redis and real worker processes; local fake gRPC and Sentry receivers.
 * Exercises a crash after durable error handoff, then restart and SDK outage recovery.
 * No workshop cluster, external Sentry project, or existing Redis instance is touched.
 */
import assert from "node:assert/strict";
import http from "node:http";
import path from "node:path";
import { once } from "node:events";
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { execFileSync, spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { gunzipSync, inflateSync } from "node:zlib";

const workerDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const project = path.resolve(workerDir, "../..");
const require = createRequire(path.join(workerDir, "package.json"));
const { createClient } = require("redis");
const grpc = require("@grpc/grpc-js");
const protoLoader = require("@grpc/proto-loader");
const tsx = require.resolve("tsx/cli");
const storage = JSON.parse(readFileSync(path.join(project, "infra/kubernetes/storage.json"), "utf8"));
const redisImage = storage.items.find(item => item.kind === "StatefulSet" && item.metadata.name === "redis")
  .spec.template.spec.containers[0].image;
assert.match(redisImage, /@sha256:[a-f0-9]{64}$/, "recovery Redis must use the pinned runtime image");

// Respect the selected runtime without loading any Sentry or database credentials.
let dockerContext = process.env.DOCKER_CONTEXT;
if (!dockerContext) {
  try {
    const line = readFileSync(path.join(project, ".env"), "utf8").split(/\r?\n/)
      .find(value => value.trim().startsWith("DOCKER_CONTEXT="));
    if (line) dockerContext = line.trim().slice("DOCKER_CONTEXT=".length).replace(/^(["'])(.*)\1$/, "$2");
  } catch (error) { if (error.code !== "ENOENT") throw error; }
}
const dockerPrefix = dockerContext ? ["--context", dockerContext] : [];
const containerName = `workshop-worker-recovery-${randomUUID().slice(0, 8)}`;
const stream = "workshop:notifications";
const group = "notification-workers";
let containerAttempted = false;
let child;
let workerStartError;
let redis;
let grpcServer;
let receiver;
let cleanupPromise;
let holdTerminal = true;
let terminalCalls = 0;
let ingestStatus = 500;
const receivedEvents = [];
let output = "";
const run = {
  runId: randomUUID(), sessionId: "isolated-recovery-test", scenario: "async-worker", workerMode: "exhaust",
  status: "queued", traceId: "fedcba0987654321fedcba0987654321", attempts: 0,
  failureService: "", errorCode: "", errorEventId: "",
  createdAt: new Date().toISOString(), updatedAt: new Date().toISOString(),
};
const producerSpanId = "0123456789abcdef";
const pause = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
function docker(args, timeout = 30000) {
  return execFileSync("docker", [...dockerPrefix, ...args], { encoding: "utf8", stdio: "pipe", timeout });
}
async function until(check, label, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (workerStartError) throw workerStartError;
    if (await check()) return;
    if (child && child.exitCode !== null) throw new Error(`worker exited ${child.exitCode}: ${output}`);
    await pause(50);
  }
  throw new Error(`Timed out waiting for ${label}; worker logs: ${output.slice(-6000)}`);
}
async function startWorker(redisPort, grpcPort, httpPort, podUid) {
  output = "";
  workerStartError = undefined;
  child = spawn(process.execPath, [tsx, "src/index.ts"], {
    cwd: workerDir,
    env: {
      ...process.env, NODE_OPTIONS: "",
      REDIS_URL: `redis://127.0.0.1:${redisPort}`,
      FULFILLMENT_ADDR: `127.0.0.1:${grpcPort}`,
      SENTRY_DSN: `http://public@127.0.0.1:${httpPort}/1`, SENTRY_RELEASE: "workshop-recovery-test",
      OTEL_EXPORTER_OTLP_ENDPOINT: `http://127.0.0.1:${httpPort}`, OTEL_SERVICE_NAME: "notification-worker",
      POD_UID: podUid, POD_NAME: podUid, POD_NAMESPACE: "isolated-recovery-test", NODE_NAME: "isolated-test",
      WORKSHOP_SESSION_ID: run.sessionId, WORKSHOP_ENVIRONMENT: "isolated-recovery-test", SERVICE_VERSION: "test",
      PORT: "0", WORKSHOP_PROTO_PATH: path.join(project, "proto/workshop.proto"),
    },
    stdio: ["ignore", "pipe", "pipe"], detached: true,
  });
  child.on("error", error => { workerStartError = error; });
  for (const pipe of [child.stdout, child.stderr]) pipe.on("data", data => { output += data.toString(); });
  await until(() => output.includes("Notification worker ready"), "worker startup");
}
async function killWorker() {
  if (!child) return;
  const worker = child;
  child = undefined;
  const exited = worker.exitCode === null && worker.signalCode === null ? once(worker, "exit") : undefined;
  // tsx spawns a Node child. Kill the dedicated process group so neither process survives the crash.
  if (worker.pid) {
    try { process.kill(-worker.pid, "SIGKILL"); } catch (error) { if (error.code !== "ESRCH") throw error; }
  }
  if (exited) await exited;
}
function cleanup() {
  return cleanupPromise ??= (async () => {
    const failures = [];
    async function attempt(action) { try { await action(); } catch (error) { failures.push(error); } }
    await attempt(killWorker);
    await attempt(async () => { if (redis?.isOpen) redis.destroy(); });
    await attempt(async () => { grpcServer?.forceShutdown(); });
    await attempt(async () => {
      if (receiver?.listening) {
        receiver.closeAllConnections();
        await new Promise(resolve => receiver.close(resolve));
      }
    });
    await attempt(async () => {
      if (!containerAttempted) return;
      try { docker(["rm", "-f", containerName]); }
      catch (error) { if (!String(error.stderr).includes("No such container")) throw error; }
    });
    if (failures.length) throw new AggregateError(failures, "Recovery-test cleanup failed");
  })();
}
for (const [signal, exitCode] of [["SIGINT", 130], ["SIGTERM", 143]]) {
  process.once(signal, () => {
    void cleanup().catch(error => console.error(error)).finally(() => process.exit(exitCode));
  });
}

try {
  containerAttempted = true;
  docker(["run", "--rm", "-d", "--name", containerName, "-p", "127.0.0.1::6379", redisImage], 60000);
  const redisPort = Number(docker(["port", containerName, "6379/tcp"]).trim().split(":").at(-1));
  redis = createClient({
    url: `redis://127.0.0.1:${redisPort}`, disableOfflineQueue: true,
    socket: { connectTimeout: 5000, reconnectStrategy: retries => retries < 50 ? 100 : new Error("Test Redis did not become ready") },
  });
  redis.on("error", () => {});
  await redis.connect();
  const definition = protoLoader.loadSync(path.join(project, "proto/workshop.proto"), {
    keepCase: false, longs: Number, enums: String, defaults: true, oneofs: true,
  });
  const descriptor = grpc.loadPackageDefinition(definition);
  grpcServer = new grpc.Server();
  grpcServer.addService(descriptor.workshop.v1.Fulfillment.service, {
    GetRun: (_call, callback) => callback(null, { ...run }),
    RecordNotificationResult: (call, callback) => {
      const request = call.request;
      if (request.outcome === "dead_lettered") {
        terminalCalls++;
        // Leave the first RPC unresolved until the worker is killed: Redis committed but Go has not.
        if (holdTerminal) return;
        run.status = "dead_lettered";
        run.failureService = "notification-worker";
        run.errorCode = request.errorCode;
        run.errorEventId = request.errorEventId;
      } else if (request.outcome === "retry") run.status = "retrying";
      else run.status = "completed";
      run.attempts = request.attempt;
      callback(null, { ...run });
    },
  });
  const grpcPort = await new Promise((resolve, reject) => {
    grpcServer.bindAsync("127.0.0.1:0", grpc.ServerCredentials.createInsecure(), (error, port) => error ? reject(error) : resolve(port));
  });
  receiver = http.createServer((request, response) => {
    const chunks = [];
    request.on("data", chunk => chunks.push(chunk));
    request.on("end", () => {
      if (request.url?.includes("/envelope/")) {
        let body = Buffer.concat(chunks);
        if (request.headers["content-encoding"] === "gzip") body = gunzipSync(body);
        if (request.headers["content-encoding"] === "deflate") body = inflateSync(body);
        for (const line of body.toString().split("\n")) {
          try { const item = JSON.parse(line); if (item.exception) receivedEvents.push(item); } catch {}
        }
        response.writeHead(ingestStatus);
      } else response.writeHead(200); // The local fake OTLP receiver accepts span batches.
      response.end("{}");
    });
  });
  receiver.listen(0, "127.0.0.1");
  await once(receiver, "listening");
  const httpPort = receiver.address().port;
  await startWorker(redisPort, grpcPort, httpPort, "first-test-pod");
  const messageId = await redis.sendCommand([
    "XADD", stream, "*", "run_id", run.runId, "session_id", run.sessionId, "scenario", run.scenario,
    "worker_mode", run.workerMode, "traceparent", `00-${run.traceId}-${producerSpanId}-01`, "tracestate", "", "baggage", "",
  ]);
  await until(() => terminalCalls >= 1, "third failure after Redis commit and before terminal RPC completion");
  const original = JSON.parse(await redis.hGet("workshop:error-outbox", run.runId));
  assert.equal(original.traceId, run.traceId);
  assert.match(original.spanId, /^[0-9a-f]{16}$/);
  assert.notEqual(original.spanId, producerSpanId);
  assert.equal(await redis.xLen("workshop:dead-letters"), 1);
  assert.equal(await redis.hGet("workshop:attempts", messageId), "3");
  assert.equal(run.status, "retrying", "the held terminal RPC has not committed");
  await killWorker();
  holdTerminal = false;
  await startWorker(redisPort, grpcPort, httpPort, "second-test-pod");
  await until(() => run.status === "dead_lettered", "pending-message reclaim and durable terminal state");
  await until(async () => (await redis.sendCommand(["XPENDING", stream, group]))[0] === 0, "ACK after durable terminal state");
  assert.equal(await redis.xLen("workshop:dead-letters"), 1, "redelivery must not append another DLQ entry");
  assert.equal(await redis.hGet("workshop:attempts", messageId), "3", "persisted retry budget remains bounded");
  assert.equal(Boolean(await redis.hExists("workshop:error-outbox", run.runId)), true, "HTTP500 must retain the SDK error");
  assert.equal(run.errorEventId, original.eventId);
  ingestStatus = 200;
  await until(async () => !(await redis.hExists("workshop:error-outbox", run.runId)), "successful SDK delivery drains the outbox");
  assert.ok(receivedEvents.length > 0, "the local Sentry receiver must receive an error envelope");
  for (const event of receivedEvents) {
    assert.equal(event.event_id, original.eventId);
    assert.equal(event.contexts.trace.trace_id, run.traceId);
    assert.equal(event.contexts.trace.span_id, original.spanId, "restart retains the original third-consumer span");
  }
  assert.equal(await redis.xLen("workshop:dead-letters"), 1);
  console.log(JSON.stringify({
    result: "PASS", receiver: "local fake gRPC/Sentry; real Redis and worker processes",
    checks: ["crash after durable error handoff", "XAUTOCLAIM recovery", "one DLQ entry", "persisted attempt budget3",
      "terminal state and ACK during SDK outage", "HTTP500 retains error", "HTTP200 drains error",
      "stable replay event ID", "original consumer trace/span survives restart"],
    eventEnvelopeSends: receivedEvents.length, terminalRpcCalls: terminalCalls,
  }));
} finally {
  await cleanup();
}
