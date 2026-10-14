# Workshop delivery

## Decisions

The workshop lasts 60 minutes: Ben opens for 15 minutes, the practical demo takes 35 minutes, and both presenters answer questions for 10 minutes. If Ben uses 10 minutes, spend the extra five on a deeper Sentry investigation. Attendee labs are separate from the timed presentation.

## Prerequisites and ordered rehearsal

1. Configure the README baseline, processed JavaScript maps, authenticated project access, and `OTEL_INFRA_LOG_SAMPLE_PERCENT=5`.
2. Run `make up`, `make test`, `make verify-collector`, `make verify`, `make verify-sentry`, and `make verify-infra`.
3. If default 120-second Sentry visibility checks time out, preserve the failed result and explicitly preflight with `WORKSHOP_VERIFICATION_TIMEOUT_SECONDS=300`; the valid 30–600 second window is recorded. Keep current-deployment evidence already visible for the presentation.
4. Run the finite `make demo-infra-sampling` and `make demo-redis-busy` exercises; retain their measured counts and real native warning as pre-session evidence.
5. Restore original exporter/propagation/sample-rate settings; verify all ten scenario permutations, then `make reset-demo` and reload for the new session.
6. Rehearse the five-card demo within 35 minutes. Confirm immediate healthy recovery after contention, current identifiers in Sentry, and shutdown limited to the project's forwarding process.

Expected results: ready workloads, source-readable origin errors, native infrastructure logs with authentic metadata, sampled routine records and retained incidents, functioning queue context, successful reset, and terminating generators.

## Timed sequence

| Time | Presenter | Content |
| --- | --- | --- |
| 0–15 | Ben | OpenTelemetry, Sentry, and their integration |
| 15–20 | Demo presenter | Architecture and Kubernetes → Collector → Sentry |
| 20–26 | Demo presenter | Complete journey and cache miss → hit |
| 26–36 | Demo presenter | Slow PostgreSQL, bounded lock timeout, linked error/trace/native logs |
| 36–44 | Demo presenter | Redis Streams, worker retry parentage, persisted carrier |
| 44–50 | Demo presenter | Log keep policy, routine 5% sampling, pre-session evidence |
| 50–60 | Both | Questions |

Read the [presenter guide](../workshop/presenter-guide.md) for concrete actions and handoff. Use the five primary cards; More examples retains the synthetic errors for follow-up. Show actual current run IDs rather than historical links.

## Attendee exercises and checkpoints

The [attendee guide](../workshop/attendee-guide.md) covers bring-up, collector parsing/routing, exclusive exporter switching, database/error correlation, supplied break/restore propagation patches, existing Redis Stream carrier, retry/pod replacement context, routine-log sampling, and a bounded native Redis warning.

Checkpoint requirements: native OTLP log context when PostgreSQL supplies it; genuine Redis server records with empty OTLP trace/span context (a standalone Sentry ingestion trace ID is not application correlation); exact origin SDK error/span links; no duplicate effects; 100% application/incident retention; lower routine-log volume; restored baseline after exercises. A tail sampler is not the log policy and is not introduced here.

During interruption rehearsal, distinguish agent-to-gateway retries from remote-endpoint retries. Use static OTLP/HTTP for the latter and restore main mode afterward; see [mode-specific validation](06-validation.md#required-assertions). Keep authentication material out of projected terminals and stored public examples.

## Completion checklist

- [x] Revised 15/35/10 flow documented
- [x] Five-card demo and separate attendee exercise guides documented
- [x] New infrastructure acceptance recorded against current deployment in both export modes
- [ ] Human 35-minute narration rehearsal completed
- [x] Main exporter, normal propagation, 5% sampling, and UI defaults restored
- [x] Automated five-card timing, full browser matrix, and fresh idle health observation recorded

Sentry history remains available after reset. Hosted-cluster validation and public publishing remain separate future work. A command-sequence rehearsal is not a live presentation to an audience.

Observed live gates: main and static infrastructure sampling/correlation, the nine-backend application Sentry matrix, the real browser error, and the finite 100%/5% comparison passed. Main mode and 5% sampling were restored. Successful visibility checks used an explicit 300-second window after the strict 120-second gate failed; transport versus indexing delay was not determined. See [the current verification record](../verification.md).

The automated five-card click-to-terminal sequence took 30 seconds, excluding narration and remote visibility. The full browser gate passed ten runs, including four collapsed additional examples and recover mode; default More examples closure and exhausted-worker selection were restored. A fresh 30-second idle observation recorded zero accepted/exported spans while health responses/probes remained healthy, and a positive-control workflow succeeded. These are automated acceptance checks, not a human narration rehearsal.
