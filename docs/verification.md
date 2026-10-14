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
make verify-collector
make verify
make verify-sentry
make verify-infra
```

Run all UI cards, using both background outcomes, and inspect their identifiers in Sentry. Repeat live gates after changes to runtime source, SDK dependencies, images, collector configuration or propagation. The detailed acceptance and interruption procedures are in [validation](plan/06-validation.md), [presenter guide](workshop/presenter-guide.md) and [attendee exercises](workshop/attendee-guide.md).

## Rehearsal completion

Propagation break and restore passed: the break patch split the catalog trace while business work completed; the restore patch recovered all five project paths and correct parentage. Reset passed twice with fresh sessions, retained PVCs, empty workshop queues/tables and reseeded fixtures. The corrected reset starts all four application workloads before waiting for dependency-aware readiness.

Scoped named-cluster deletion followed by a fresh `make up` passed in 151.06 seconds, including both automatic processed source-map uploads and the application/network matrix. A repeated deployment passed in 37.774 seconds, retained business rows and PVCs, and replaced all four application pod UIDs. Global kubeconfig/Docker configuration and unrelated containers were unchanged. The final eight-case Sentry gate passed after the repeated deployment, using native log IDs. Main exporter mode, normal propagation and loopback forwarding were restored.

All nine original numbered plan completion checklists were complete at that baseline. This is a rehearsal of the documented startup/reset/demo sequence; it is not a live 60-minute presentation to an audience.

## Tested environment

macOS/arm64, healthy existing Colima allocation 7 CPUs / 18 GiB, one kind node, Kubernetes 1.36.4, Collector Contrib 0.162.0 and Envoy Gateway 1.9.2. The documented attendee 4 CPU / 8 GiB allocation is a recommendation, not a separately benchmarked result. Global kubeconfig, Colima settings and unrelated clusters are preserved. GitHub publication and hosted Kubernetes verification were outside scope.

## Verified local release

The presenter's ignored `.env` pins `SERVICE_VERSION` and `SENTRY_RELEASE` to the currently inspected application release. The initial verification above records the original release; `.local/verification/health-checks.json` records the subsequent health-check sampling deployment. Later documentation, verification and reset-orchestration commits do not change those runtime artifacts. A fresh checkout defaults these fields to its current Git commit. Before changing application source or dependencies, remove the two local overrides (or set both to the new committed application version), then rebuild, deploy and repeat the live gates. Keep SDK release, resource version and uploaded source maps aligned.

The final snapshot had 10 ready workshop pods and a kind node using about 1.8 GiB of the Docker runtime's 17.5 GiB usable memory. Host workspace free space was 233 GiB; the existing VM filesystem had 11 GiB free after rehearsal. The attendee recommendation is 20 GiB free before setup. The existing runtime profile and unrelated Docker data were preserved, and no Docker pruning was performed. This snapshot is not a peak-build benchmark.

The fresh browser build was independently inspected through the Sentry plugin: its SDK error resolved to original `page.tsx:201`, linked to the exact browser action span, and shared the backend trace and native log context. Read-only API verification retries HTTP429/500/502/503/504 at most three times with short backoff; other HTTP errors retain their original behavior. This query retry does not change collector delivery or application state.

## Infrastructure workshop revision — observed acceptance

The revised delivery is 15 minutes for Ben, 35 minutes for the five-card practical demo, and 10 minutes for questions. The new `postgres-lock-timeout` scenario uses a real 750 ms lock timeout, no order/stock change, and bounded automatic recovery. PostgreSQL/Redis native logs retain infrastructure routing and originating pod identity; validated PostgreSQL `application_name` supplies native `catalog.product_query` span context.

Application traces/logs stay at 100% with health exclusions. Routine PostgreSQL/Redis logs use configured 5% sampling (also allowing 10/100), while infrastructure warnings/errors and recognized PostgreSQL slow-query/lock-wait evidence remain at 100%. Both exclusive exporters use the same policy; no trace tail sampler is introduced.

### Source, behavior, and live evidence

| Gate | Observed result | Evidence boundary |
| --- | --- | --- |
| Catalog tests | 12 passed: 9 unit and 3 real PostgreSQL tests, including cancellation and idle-timeout protection | Source and disposable real-database checks |
| Web tests/typecheck | Six tests passed; typecheck passed | Source contract and health-sampling regression checks |
| Fresh browser UI matrix | Five primary cards, all four More examples cards, and recover-on-attempt-3 passed: ten distinct runs/traces with correct origins, states, and event IDs | `.local/verification/browser-revision.json` |
| Automated primary-card timing | First click to last terminal state took 30 seconds; excludes narration and remote telemetry visibility | Actual browser clicks across the five core cards |
| Fresh idle health observation | Twelve direct health responses returned 200 plus native probes; zero accepted/exported spans during a 30-second idle window; normal positive-control workflow succeeded | `.local/verification/health-checks.json` |
| Pinned Collector fixture checks | Six cases passed, including fresh initialization, native timestamps/context, and unique application record IDs | Actual pinned Collector image/configuration |
| Routine fixture sampling at 5% | PostgreSQL 102/2,000; Redis 101/2,000 | Statistical fixture counts |
| Go, worker recovery, and network matrix | Passed transaction/queue, process-recovery, and allowed/denied-network assertions | Isolated tests plus actual cluster checks |
| JavaScript maps and browser error | Builds uploaded and processed maps; fresh browser error resolved to original `page.tsx:215` and continued into the server trace | Current build plus real browser/Sentry inspection |
| Main-mode application Sentry matrix | Nine backend runs, 392 spans, 138 logs; application/error/native-context assertions passed | `.local/verification/sentry-revision-main.json` |
| Main-mode infrastructure live gate | 102/2,000 routine records; warning/error retention 100%; application log completeness: slow 18/18, lock 8/8, healthy 18/18 | `.local/verification/infra-live-sentry.json` |
| Static OTLP/HTTP infrastructure live gate | 104/2,000 routine records; warning/error retention 100%; application log completeness: slow 18/18, lock 8/8, healthy 18/18 | `.local/verification/infra-live-otlphttp.json` |
| Native PostgreSQL incidents in both routes | Slow-duration, LOG-level lock-wait, and SQLSTATE `55P03` error records retained with validated business-span context and PostgreSQL pod identity | Both infrastructure live records |
| Finite main-mode sampling comparison | 2,000/2,000 at 100%; 98/2,000 at 5%; one warning and one error retained in both batches; restored prior rate 5% | `.local/verification/infra-sampling.json` |
| Redis busy incident in both routes | Approximately 403 ms in main mode and 400.838 ms in static mode; genuine warning retained, PING recovered, no restart | Both infrastructure live records and `.local/verification/redis-busy.json` |

The actual running candidate uses the same built images throughout these live gates; exact version/image identities and run/trace/event/log identifiers are retained in ignored evidence. A successful deployment restored main Sentry Exporter mode afterward, with normal propagation and 5% routine-log sampling. All generators terminated. The initial 2026-10-09 tables above are historical and do not substitute for this revision's checks.

### Native Redis context and service filters

Collector fixtures prove that Redis native logs have empty OTLP trace/span context. Sentry can assign a standalone ingestion trace ID while `span_id` is absent; that ID is not evidence of application correlation. Investigate Redis server records by `resource.service.name:redis`, authentic pod identity, and incident time. Native PostgreSQL filters use `resource.service.name:postgres`; application records also have a `service.name` attribute.

### Visibility limitation and remaining rehearsal

The strict 120-second visibility gate failed; required records were visible around three minutes later. Transport delay versus Sentry indexing delay was not determined. Successful live gates explicitly used `WORKSHOP_VERIFICATION_TIMEOUT_SECONDS=300`, recorded in evidence. Default remains 120 seconds, with valid integer values 30–600. Later success does not retrospectively satisfy the failed strict deadline. The visibility window does not alter collector retry limits, application deadlines, or source-map processing waits.

The current native/application live acceptance, full browser matrix, and fresh idle health-check observation are complete. The five core browser actions ran from first click to last terminal state in 30 seconds, excluding spoken narration and remote telemetry visibility. More examples was initially closed, the worker default was exhaustion, and both defaults were restored after checking all cases. Browser console output contained only the expected deliberately injected HTTP 502/500 responses.

No human 35-minute narration rehearsal or live presentation to an audience has been performed. The guide allocates that time and uses preflight evidence already visible in Sentry. The automated 30-second action sequence is not a measurement of the narrated demonstration.

Routine sampling verification matches only the native message prefix `Workshop sampling probe <batch> record *`; diagnostic slow-DO records containing the batch ID are excluded from routine counts. A read-only check of the existing main-mode batch confirmed that this narrower predicate retains the recorded 102/2,000 count.
