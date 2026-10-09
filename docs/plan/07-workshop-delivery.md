# Workshop delivery

## Decisions

60-minute presenter guide plus separate attendee labs and working checkpoints.

## Steps

Allocate 5m architecture, 10m pipeline, 10m healthy/cache, 10m DB, 10m errors, 10m async/Kubernetes, 5m questions; rehearse fresh startup/reset.

## Acceptance

Presenter can start, demonstrate and reset from README; labs show exporter switching, broken/restored propagation and changed pod attributes.

## Completion checklist

- [ ] Implementation complete
- [ ] Automated checks complete
- [ ] Live verification recorded where applicable
- [ ] Documentation matches observed behavior

## Delivery artifacts

Presenter guide and attendee guide live in docs/workshop. Presenter timings total60m; attendee exercises are separate. Working baseline is restored after exporter and propagation experiments. Show actual current runIDs instead of historical links.

Exercise checkpoints: cluster readiness; native log correlation; exclusive export switching; error-to-span navigation; broken/restored carrier; async/pod restart identity. Rehearse `make up`, all scenarios, verification and reset before delivery.

## Delivery sequence and prerequisites

Read the [presenter guide](../workshop/presenter-guide.md) for the timed60-minute flow and the [attendee guide](../workshop/attendee-guide.md) for separate labs. Rehearse `make up`, `make verify`, `make source-map-upload`, `make verify-sentry`, then `make reset-demo` before presenting. Keep the browser and Sentry project views available.

Time budget:5min architecture,10min collection/export,10min healthy/cache,10min slow SQL,10min linked errors/logs,10min async/pod context,5min questions. Keep identifiers on screen and explain why each observed span/log answers the debugging question.

Attendee labs: bring-up, collector parsing/routing, exclusive exporter switching, error correlation, supplied break/restore propagation patches, retry/pod replacement context. Each lab has a checkpoint; the known-good restore patch ends the intentional breakage. The control centre does not expose Kubernetes commands or arbitrary injection controls.

Before finishing, verify reset creates a new session with fixtures and that forwarding shutdown affects only this workshop. Sentry history remains available. Hosted cluster validation and public publication are separate future work.
