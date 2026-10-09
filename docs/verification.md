# Verification record — 2026-10-09

The application release verified locally is `6f4c13254eb81e0f1e40ccfe7776639963a491e9`. This report contains sanitized results; exact run, trace, event and log identifiers remain in ignored `.local/verification`.

## Application and telemetry

| Gate | Observed result |
| --- | --- |
| Service and contract tests | Catalog 4, web 3, worker 5 and Go unit tests passed; locked dependency installation succeeded |
| Go integration/race tests | Transaction rollback, one origin capture, duplicate submission/publication, persisted producer context, terminal-state immutability and restricted-role migrations passed |
| Nine browser permutations | All eight cards, including both background outcomes, reached expected states with distinct run and trace identifiers |
| Eight backend permutations | SQL/order/inventory/outbox/queue/cache invariants passed |
| Live Sentry spans and logs | Correct project routing, HTTP/gRPC/queue parentage, original pod metadata and native log trace/span fields passed |
| Deliberate errors | Python, Go, Next.js, browser and terminal worker events linked to their active exported span; readable original source was confirmed |
| Automatic source maps | Both host npm builds and production Docker builds uploaded maps and waited for completed Sentry processing; no manual upload was needed |
| Worker recovery | Real worker processes plus disposable Redis passed crash/reclaim, stable event ID/original span, one DLQ, terminal ACK during SDK HTTP500, and later SDK HTTP200 drain |
| Live queued work | A stopped cluster worker resumed pending work with one order, one inventory decrement and one completion |
| Agent restart | Checkpoint recovery retained marked telemetry without replaying original stdout records |
| Main-mode gateway outage | Business work completed while the agent retried the unavailable gateway; marked trace/log evidence arrived after restoration |
| Static remote endpoint outage | Five completed workflows survived blocked gateway HTTPS, a gateway restart and restored egress: 47 spans / 16 logs per workflow, every original stdout UUID once |
| Network/RBAC | Named allowed paths worked; prohibited paths and secret/unrelated-namespace reads failed |
| Dependency/privacy checks | Both npm audits reported zero vulnerabilities; public-source and final-image credential scans passed |

The process-crash harness uses local fake gRPC/Sentry receivers. Real Sentry ingestion is verified separately against application workflows. A deliberate browser error requires the real browser; `make verify-sentry` covers the eight backend permutations.

## Ingestion and recovery boundaries

Sentry query results can repeat an identical native log identity. Verification collapses only fully identical rows with the same native Sentry ID. It rejects conflicting rows for one ID and still rejects one application `log.record_id` arriving under different native IDs. This preserves the duplicate-delivery check.

The pinned main Sentry Exporter persists unsent batches but does not retry a batch after a failed Sentry HTTP request. The main gateway-down rehearsal establishes agent-to-gateway retry, not remote endpoint resilience. Static OTLP HTTP mode enables bounded transient-error retries and a persistent queue; its separate blocked-endpoint/restart rehearsal recovered every marked record. See [the implementation and recovery limits](plan/04-collector-and-sentry.md#retry-and-persistence-limits).

## Reproduction

```sh
make test
make verify-recovery
make up
make verify
make verify-sentry
```

Run all UI cards, using both background outcomes, and inspect their identifiers in Sentry. Repeat live gates after changes to runtime source, SDK dependencies, images, collector configuration or propagation. The detailed acceptance and interruption procedures are in [validation](plan/06-validation.md), [presenter guide](workshop/presenter-guide.md) and [attendee exercises](workshop/attendee-guide.md).

## Rehearsal completion

Propagation break and restore passed: the break patch split the catalog trace while business work completed; the restore patch recovered all five project paths and correct parentage. Reset passed twice with fresh sessions, retained PVCs, empty workshop queues/tables and reseeded fixtures. The corrected reset starts all four application workloads before waiting for dependency-aware readiness.

Scoped named-cluster deletion followed by a fresh `make up` passed in 151.06 seconds, including both automatic processed source-map uploads and the application/network matrix. A repeated deployment passed in 37.774 seconds, retained business rows and PVCs, and replaced all four application pod UIDs. Global kubeconfig/Docker configuration and unrelated containers were unchanged. The final eight-case Sentry gate passed after the repeated deployment, using native log IDs. Main exporter mode, normal propagation and loopback forwarding were restored.

All nine numbered plan completion checklists are complete. This is a rehearsal of the documented startup/reset/demo sequence; it is not a live 60-minute presentation to an audience.

## Tested environment

macOS/arm64, healthy existing Colima allocation 7 CPUs / 18 GiB, one kind node, Kubernetes 1.36.4, Collector Contrib 0.162.0 and Envoy Gateway 1.9.2. The documented attendee 4 CPU / 8 GiB allocation is a recommendation, not a separately benchmarked result. Global kubeconfig, Colima settings and unrelated clusters are preserved. GitHub publication and hosted Kubernetes verification were outside scope.

## Verified local release

The presenter's ignored `.env` pins `SERVICE_VERSION` and `SENTRY_RELEASE` to the inspected application release above. Later documentation, verification and reset-orchestration commits do not change those runtime artifacts. A fresh checkout defaults these fields to its current Git commit. Before changing application source or dependencies, remove the two local overrides (or set both to the new committed application version), then rebuild, deploy and repeat the live gates. Keep SDK release, resource version and uploaded source maps aligned.

The final snapshot had 10 ready workshop pods and a kind node using about 1.8 GiB of the Docker runtime's 17.5 GiB usable memory. Host workspace free space was 233 GiB; the existing VM filesystem had 11 GiB free after rehearsal. The attendee recommendation is 20 GiB free before setup. The existing runtime profile and unrelated Docker data were preserved, and no Docker pruning was performed. This snapshot is not a peak-build benchmark.

The fresh browser build was independently inspected through the Sentry plugin: its SDK error resolved to original `page.tsx:201`, linked to the exact browser action span, and shared the backend trace and native log context. Read-only API verification retries HTTP429/500/502/503/504 at most three times with short backoff; other HTTP errors retain their original behavior. This query retry does not change collector delivery or application state.
