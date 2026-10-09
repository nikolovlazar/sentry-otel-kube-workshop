# Outcome and implementation gates

## Decisions

Create a local public-ready repository, no GitHub publishing. Fully verify local Kubernetes, browser/server traces, JSON stdout logs and SDK errors in Sentry.

## Steps

Create foundation and shared contracts; bootstrap Kubernetes; prove telemetry compatibility; implement workflows; validate and rehearse.

## Acceptance

Healthy distributed trace; exact-span error links; native log trace context; correct application pod attribution; no duplicate exports.

## Completion checklist

- [ ] Implementation complete
- [ ] Automated checks complete
- [ ] Live verification recorded where applicable
- [ ] Documentation matches observed behavior

## Ordered milestones

1. Foundation: local Git repository, MIT license, configuration placeholders, protobuf and all numbered docs.
2. Kubernetes: checksummed kind/kubectl/Helm, isolated single-node cluster, storage, Gateway API and two Collector roles.
3. Compatibility: a real browser-triggered run across all languages; SDK errors, native log context and project routing.
4. Workflows: deterministic cache/DB/error cards, transactional outbox, single consumer and idempotent retry/DLQ.
5. Acceptance: service tests, database/queue invariants, network isolation, restarts and live Sentry evidence.
6. Delivery: readable JavaScript source, presenter and attendee guides, clean startup/reset rehearsal and public audit.

Each gate requires observed behavior; configuration alone is not live verification. GitHub publication and hosted Kubernetes are excluded.

## Milestones and dependencies

1. Save this design and the shared contract. Commit only public source; local configuration remains ignored.
2. Bootstrap checked tools, create the named kind cluster, then deploy storage and the collector/Envoy foundations.
3. Prove a minimal complete journey in Sentry before accepting additional scenarios. HTTP, gRPC and queue hops must keep one trace ID.
4. Add deterministic cache, latency and origin failures, then prove business invariants independently of telemetry.
5. Verify each signal in its intended Sentry project, including active span IDs and readable source. Rehearse interruptions and exclusive exporter switching.
6. Rehearse reset and repeated deployment, finish the presenter/attendee guides, and audit public files.

The collector cannot replace SDK exception capture. The application never waits for Sentry indexing to determine a run's business state. Verification polls live evidence separately.

## Files to inspect

The nine numbered files in this directory follow the implementation order. Each references runnable commands, prerequisites and acceptance criteria. The source tree keeps protocol definitions in `proto`, collection and networking in `infra`, operational commands in `scripts`, and language-specific code under `apps`/`services`.
