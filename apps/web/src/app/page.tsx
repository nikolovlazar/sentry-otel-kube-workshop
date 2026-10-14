"use client";

import * as Sentry from "@sentry/nextjs";
import { useEffect, useState } from "react";
import { initializeBrowserSentry as ensureBrowserSentry } from "@/lib/browser-sentry";
import {
  terminalStatuses,
  type PublicConfig,
  type Run,
  type Scenario,
  type WorkerMode,
} from "@/lib/types";

const cards: {
  id: Scenario;
  number: string;
  title: string;
  description: string;
  path: string;
  lesson: string;
  tone?: string;
}[] = [
  {
    id: "full-journey",
    number: "01",
    title: "Complete journey",
    description:
      "One request crosses the whole application, then finishes in a background worker.",
    path: "Web → Catalog → Fulfillment → Worker",
    lesson: "Distributed trace",
  },
  {
    id: "cache-miss-hit",
    number: "02",
    title: "Cache miss → hit",
    description:
      "Fetch the same product twice. Compare the database query with a Redis cache hit.",
    path: "Catalog → Redis + PostgreSQL",
    lesson: "Cache visibility",
  },
  {
    id: "slow-postgres",
    number: "03",
    title: "Slow database",
    description:
      "Find the slow query, then connect it with PostgreSQL’s own duration log.",
    path: "Catalog → PostgreSQL · +1.2 seconds",
    lesson: "Find the bottleneck",
  },
  {
    id: "postgres-lock-timeout",
    number: "04",
    title: "Database contention",
    description:
      "A bounded database lock blocks a product read. Connect the timeout, trace, and native logs.",
    path: "Catalog → PostgreSQL · 750 ms lock timeout",
    lesson: "Infrastructure explains errors",
    tone: "error",
  },
  {
    id: "python-error",
    number: "06",
    title: "Python exception",
    description:
      "The catalog fails. Follow its error back to the request and its structured logs.",
    path: "Web → Catalog · deliberate exception",
    lesson: "Error + trace + logs",
    tone: "error",
  },
  {
    id: "go-error",
    number: "07",
    title: "Go exception",
    description:
      "Fulfillment fails over gRPC and returns the original Sentry event ID to the web app.",
    path: "Catalog → Fulfillment · gRPC failure",
    lesson: "Error at its origin",
    tone: "error",
  },
  {
    id: "async-worker",
    number: "05",
    title: "Background job",
    description:
      "Watch durable queue delivery, retry attempts, and a final success or dead letter.",
    path: "Fulfillment → Redis Streams → Worker",
    lesson: "Trace across a queue",
  },
  {
    id: "next-error",
    number: "08",
    title: "Next.js exception",
    description:
      "Trigger a server exception before checkout. The error keeps the active OTel context.",
    path: "Web server · deliberate exception",
    lesson: "Server error context",
    tone: "error",
  },
  {
    id: "browser-error",
    number: "09",
    title: "Browser exception",
    description:
      "A client error shares its trace with the server request that started the workflow.",
    path: "Browser → Web · client exception",
    lesson: "Browser-to-server context",
    tone: "error",
  },
];
const primaryCards = cards.filter((card) =>
  [
    "full-journey",
    "cache-miss-hit",
    "slow-postgres",
    "postgres-lock-timeout",
    "async-worker",
  ].includes(card.id),
);
const additionalCards = cards.filter((card) => !primaryCards.includes(card));
const friendlyStatus: Record<string, string> = {
  running: "Running",
  queued: "Queued",
  retrying: "Retrying",
  completed: "Completed",
  failed: "Failed",
  dead_lettered: "Dead letter",
};
async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, { cache: "no-store", ...options });
  const data = await response.json();
  if (!response.ok && !(data && typeof data.runId === "string"))
    throw new Error(data.error ?? "Request failed");
  return data as T;
}
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
function short(value: string) {
  return value ? `${value.slice(0, 8)}…${value.slice(-4)}` : "—";
}
function Clock({ date }: { date: string }) {
  return (
    <>
      {date
        ? new Date(date).toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
          })
        : "—"}
    </>
  );
}

export default function ControlCentre() {
  const [config, setConfig] = useState<PublicConfig | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [selected, setSelected] = useState<Run | null>(null);
  const [busy, setBusy] = useState<Scenario | null>(null);
  const [workerMode, setWorkerMode] = useState<WorkerMode>("exhaust");
  const [notice, setNotice] = useState("");
  const [copied, setCopied] = useState("");
  useEffect(() => {
    let live = true;
    ensureBrowserSentry()
      .then(async (value) => {
        if (!live) return;
        setConfig(value);
        const result = await request<{ runs: Run[] }>(
          `/api/runs?sessionId=${encodeURIComponent(value.sessionId)}`,
        );
        if (live) setRuns(result.runs);
      })
      .catch((error) => {
        if (live) setNotice(error.message);
      });
    return () => {
      live = false;
    };
  }, []);
  function remember(run: Run) {
    setSelected(run);
    setRuns((current) =>
      [run, ...current.filter((item) => item.runId !== run.runId)].slice(0, 20),
    );
  }
  async function launch(scenario: Scenario) {
    if (busy) return;
    setBusy(scenario);
    setNotice("");
    try {
      const settings = config ?? (await ensureBrowserSentry());
      let run = await Sentry.startNewTrace(() =>
        Sentry.startSpan(
          {
            name: `workshop.action ${scenario}`,
            op: "ui.action",
            attributes: {
              "workshop.scenario": scenario,
              "workshop.step": "ui.action",
              "workshop.session_id": settings.sessionId,
            },
          },
          async (span) => {
            const started = await request<Run>("/api/runs", {
              method: "POST",
              headers: { "content-type": "application/json" },
              body: JSON.stringify({
                scenario,
                workerMode:
                  scenario === "async-worker" ? workerMode : "success",
                sessionId: settings.sessionId,
              }),
            });
            span.setAttributes({
              "workshop.run_id": started.runId,
              "workshop.trace_id": started.traceId,
            });
            if (scenario !== "browser-error") return started;
            // This caught exception is captured once while the initiating browser span is active.
            let eventId = "";
            try {
              throw new Error(
                "Deliberate browser workflow failure: demo control failed to update",
              );
            } catch (error) {
              eventId = Sentry.withScope((scope) => {
                scope.setTags({
                  "workshop.run_id": started.runId,
                  "workshop.scenario": scenario,
                  "workshop.session_id": settings.sessionId,
                });
                return Sentry.captureException(error);
              });
            }
            return request<Run>(`/api/runs/${started.runId}/browser-error`, {
              method: "POST",
              headers: { "content-type": "application/json" },
              body: JSON.stringify({ errorEventId: eventId }),
            });
          },
        ),
      );
      remember(run);
      for (
        let tick = 0;
        tick < 15 && !terminalStatuses.has(run.status);
        tick++
      ) {
        await pause(1000);
        run = await request<Run>(`/api/runs/${run.runId}`);
        remember(run);
      }
      if (!terminalStatuses.has(run.status))
        setNotice(
          "The workflow is still running after 15 seconds. Select Refresh to check its durable state.",
        );
    } catch (error) {
      setNotice(
        error instanceof Error
          ? error.message
          : "The workflow could not start.",
      );
    } finally {
      setBusy(null);
    }
  }
  async function refresh() {
    if (!selected) return;
    try {
      remember(await request<Run>(`/api/runs/${selected.runId}`));
      setNotice("");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Refresh failed.");
    }
  }
  async function copy(value: string, label: string) {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(label);
      setTimeout(() => setCopied(""), 1800);
    } catch {
      setNotice("Select the identifier to copy it.");
    }
  }
  function renderCard(card: (typeof cards)[number]) {
    return (
      <article className={`card ${card.tone ?? ""}`} key={card.id}>
        <div className="card-top">
          <span className="card-number">{card.number}</span>
          <span className="lesson">{card.lesson}</span>
        </div>
        <h3>{card.title}</h3>
        <p>{card.description}</p>
        <div className="card-path">{card.path}</div>
        {card.id === "async-worker" && (
          <label className="worker-mode">
            Worker outcome
            <select
              value={workerMode}
              onChange={(event) =>
                setWorkerMode(event.target.value as WorkerMode)
              }
              disabled={Boolean(busy)}
            >
              <option value="success">Succeed first attempt</option>
              <option value="recover">Recover on attempt 3</option>
              <option value="exhaust">Exhaust → dead letter</option>
            </select>
          </label>
        )}
        <button
          onClick={() => launch(card.id)}
          disabled={Boolean(busy) || !config}
          className="run-button"
        >
          <span>
            {busy === card.id ? "Running workflow…" : "Run workflow"}
          </span>
          <span aria-hidden="true">{busy === card.id ? "◌" : "↗"}</span>
        </button>
      </article>
    );
  }
  const orgBase = config?.orgSlug
    ? `${config.baseUrl.replace(/\/$/, "")}/organizations/${encodeURIComponent(config.orgSlug)}`
    : "";
  const traceLink =
    selected?.traceId && orgBase
      ? `${orgBase}/explore/traces/trace/${selected.traceId}/`
      : "";
  const errorLink =
    selected?.errorEventId && orgBase
      ? `${orgBase}/issues/?query=${encodeURIComponent(selected.errorEventId)}`
      : "";
  const logLink =
    selected?.traceId && orgBase
      ? `${orgBase}/explore/logs/?logsQuery=${encodeURIComponent(`trace:${selected.traceId}`)}`
      : "";
  return (
    <main>
      <header className="topbar">
        <a className="brand" href="/">
          <span className="brand-mark">⌘</span>
          <span>
            Connected Context <small>WORKSHOP CONTROL CENTRE</small>
          </span>
        </a>
        <div className="session">
          <span className="live-dot" /> {config?.environment ?? "Connecting"}{" "}
          <span className="session-name">/ {config?.sessionId ?? "…"}</span>
        </div>
      </header>
      <section className="hero">
        <div className="eyebrow">
          <span>OPENTELEMETRY + SENTRY</span>
          <span className="hero-tag">Kubernetes lab</span>
        </div>
        <h1>
          One workflow.
          <br />
          <span>Every piece of context.</span>
        </h1>
        <p>
          Start a real distributed workflow. See how traces connect services,
          logs explain the path, and errors point to the failure.
        </p>
        <div className="hero-footer">
          <span>
            <i className="signal-dot purple" /> Traces
          </span>
          <span>
            <i className="signal-dot orange" /> Logs
          </span>
          <span>
            <i className="signal-dot pink" /> Errors
          </span>
          <a
            href="https://docs.sentry.io/concepts/otlp/"
            target="_blank"
            rel="noreferrer"
          >
            How the signals connect ↗
          </a>
        </div>
        <div className="hero-grid" aria-hidden="true" />
        <div className="orb" aria-hidden="true" />
      </section>
      <section className="architecture" aria-label="Application architecture">
        <div className="section-kicker">THE APPLICATION</div>
        <div className="topology">
          <div>
            <b>Browser + Web</b>
            <small>Next.js · JavaScript</small>
          </div>
          <span>→</span>
          <div>
            <b>Catalog</b>
            <small>Python · HTTP</small>
          </div>
          <span>→</span>
          <div>
            <b>Fulfillment</b>
            <small>Go · gRPC</small>
          </div>
          <span>⇢</span>
          <div>
            <b>Notification</b>
            <small>Node.js · Redis Streams</small>
          </div>
        </div>
        <div className="topology-base">
          <span>PostgreSQL</span>
          <span>Redis cache + queue</span>
          <span>Kubernetes networking</span>
          <span>OTel Collector → Sentry</span>
        </div>
      </section>
      <section className="scenarios">
        <div className="section-heading">
          <div>
            <div className="section-kicker">TRY A WORKFLOW</div>
            <h2>Make the system tell its story.</h2>
          </div>
          <span className="section-note">Five workflows. Real requests.</span>
        </div>
        <div className="cards">
          {primaryCards.map(renderCard)}
        </div>
        <details className="more-examples">
          <summary>
            More examples <span>Python, Go, Next.js, and browser exceptions</span>
          </summary>
          <div className="cards">{additionalCards.map(renderCard)}</div>
        </details>
      </section>
      <section className="results" aria-live="polite">
        <div className="section-heading">
          <div>
            <div className="section-kicker">CONNECTED CONTEXT</div>
            <h2>Your workflow, end to end.</h2>
          </div>
          {selected && (
            <button
              className="text-button"
              onClick={refresh}
              disabled={Boolean(busy)}
            >
              Refresh ↻
            </button>
          )}
        </div>
        {notice && (
          <div role="status" className="notice">
            {notice}
          </div>
        )}
        {selected ? (
          <div className="run-detail">
            <div className="run-title">
              <div>
                <span className="detail-label">SELECTED WORKFLOW</span>
                <h3>
                  {cards.find((card) => card.id === selected.scenario)?.title ??
                    selected.scenario}
                </h3>
              </div>
              <span className={`status ${selected.status}`}>
                {friendlyStatus[selected.status] ?? selected.status}
              </span>
            </div>
            <dl className="identifiers">
              <div>
                <dt>Run ID</dt>
                <dd>
                  <code>{selected.runId}</code>
                  <button
                    aria-label="Copy run ID"
                    onClick={() => copy(selected.runId, "Run ID")}
                  >
                    {copied === "Run ID" ? "Copied" : "Copy"}
                  </button>
                </dd>
              </div>
              <div>
                <dt>Trace ID</dt>
                <dd>
                  <code>{selected.traceId || "Not available"}</code>
                  {selected.traceId && (
                    <button
                      aria-label="Copy trace ID"
                      onClick={() => copy(selected.traceId, "Trace ID")}
                    >
                      {copied === "Trace ID" ? "Copied" : "Copy"}
                    </button>
                  )}
                </dd>
              </div>
              <div>
                <dt>Sentry event</dt>
                <dd>
                  <code>{selected.errorEventId || "No error captured"}</code>
                  {selected.errorEventId && (
                    <button
                      aria-label="Copy event ID"
                      onClick={() => copy(selected.errorEventId, "Event ID")}
                    >
                      {copied === "Event ID" ? "Copied" : "Copy"}
                    </button>
                  )}
                </dd>
              </div>
            </dl>
            <div className="run-metadata">
              <span>
                Worker attempts <b>{selected.attempts}/3</b>
              </span>
              <span>
                Origin <b>{selected.failureService || "—"}</b>
              </span>
              <span>
                Error code <b>{selected.errorCode || "—"}</b>
              </span>
              <span>
                Started{" "}
                <b>
                  <Clock date={selected.createdAt} />
                </b>
              </span>
            </div>
            <div className="sentry-links">
              {traceLink && (
                <a href={traceLink} target="_blank" rel="noreferrer">
                  Open trace in Sentry ↗
                </a>
              )}
              {logLink && (
                <a href={logLink} target="_blank" rel="noreferrer">
                  Related logs ↗
                </a>
              )}
              {errorLink && (
                <a href={errorLink} target="_blank" rel="noreferrer">
                  Captured error ↗
                </a>
              )}
              {!orgBase && (
                <span>Configure SENTRY_ORG_SLUG to enable Sentry links.</span>
              )}
            </div>
          </div>
        ) : (
          <div className="empty-state">
            <span className="empty-glyph">⌁</span>
            <h3>Context starts with a request.</h3>
            <p>
              Choose a workflow above. Its trace, error, and worker state will
              appear here.
            </p>
          </div>
        )}
        {runs.length > 0 && (
          <div className="history">
            <div className="history-heading">
              <h3>Recent runs</h3>
              <span>Latest {runs.length} in this session</span>
            </div>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Workflow</th>
                    <th>Run</th>
                    <th>Status</th>
                    <th>Attempts</th>
                    <th>Started</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((run) => (
                    <tr
                      key={run.runId}
                      className={
                        selected?.runId === run.runId ? "selected" : ""
                      }
                    >
                      <td>
                        <button
                          onClick={() => setSelected(run)}
                          disabled={Boolean(busy)}
                        >
                          {cards.find((card) => card.id === run.scenario)
                            ?.title ?? run.scenario}
                        </button>
                      </td>
                      <td>
                        <code>{short(run.runId)}</code>
                      </td>
                      <td>
                        <span className={`status ${run.status}`}>
                          {friendlyStatus[run.status] ?? run.status}
                        </span>
                      </td>
                      <td>{run.attempts}</td>
                      <td>
                        <Clock date={run.createdAt} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </section>
      <footer>
        <span>Connected Context Workshop</span>
        <span>
          Application traces + container logs → OTel Collector → Sentry
        </span>
        <a
          href="https://sentry.io/resources/otel-connected-context-workshop/"
          target="_blank"
          rel="noreferrer"
        >
          Workshop ↗
        </a>
      </footer>
    </main>
  );
}
