# Local Kubernetes

## Decisions

Use the selected existing Docker-compatible runtime context; Colima is the presenter default. Pinned local tools and ignored kubeconfig isolate this project. Single-node kind, Envoy Gateway and three namespaces.

## Steps

Run make bootstrap/doctor/cluster-up; deploy PostgreSQL and Redis PVCs; install Envoy; apply collector RBAC and manifests; establish localhost:8080 forwarding.

## Acceptance

Node ready; Services resolve; only web is exposed; allowed and denied NetworkPolicy probes pass; wrong-context operations abort.

## Completion checklist

- [x] Implementation complete
- [x] Automated checks complete
- [x] Live verification recorded where applicable
- [x] Documentation matches observed behavior

## Commands and isolation

`make bootstrap` installs tools into `.local/bin`. `make cluster-up` creates `sentry-otel-workshop`; kubeconfig is `.local/kubeconfig`. Every mutation supplies this path and checks context `kind-sentry-otel-workshop`. Docker uses the selected context without changing its global default.

Namespaces: application/data in `sentry-otel-workshop`; collectors in `observability`; Envoy controller in `envoy-gateway-system`. Envoy proxy stays ClusterIP and port-forward binds only localhost:8080. PostgreSQL/Redis use PVCs; collector gateway persists unsent queued batches and agent keeps log checkpoints.

One replica per custom workload. Agents tolerate the single control-plane node. Application pods have no API access tokens; collector RBAC reads pod/replicaset metadata, not Secrets. The agent's log/checkpoint filesystem access is a documented exception to non-root application pods.

Readiness, allowed/denied NetworkPolicy probes, repeated deploy and wrong-context abort are mandatory. Cleanup never changes global kubeconfig, prunes Docker, stops Colima or deletes other clusters.

## Egress rules

`network-egress.json` limits each custom application to its named dependencies, DNS, the node collector and public HTTPS for SDK ingestion. PostgreSQL and Redis initiate no outbound connections. Migration Jobs reach only DNS and PostgreSQL. The agent reaches its gateway and Kubernetes API on TLS ports; the gateway reaches public HTTPS. Standard NetworkPolicy cannot select Sentry DNS names, so public TCP443 is allowed with private address ranges excluded. Envoy keeps egress to its controller for xDS; its incoming application port is isolated separately. These are local workshop policies, with explicit live positive and negative connectivity tests.

## Prerequisites, commands and expected results

1. Start an existing Docker-compatible runtime; select it with `DOCKER_CONTEXT` in ignored `.env`. No runtime profile settings are changed. Provide 4 CPUs, 8GiB VM memory and 20GiB free disk.
2. Run `make bootstrap`. It downloads checksummed kind/kubectl/Helm/Node into `.local/bin` for the host's supported architecture.
3. Run `make doctor`. Configure `SENTRY_SOURCE_MAP_AUTH_TOKEN` for the required build-time upload. Tool pins and runtime capacity must pass; configuration errors name keys without printing their values.
4. Run `make cluster-up`. The named single-node kind cluster uses Kubernetes1.36.4 and its dedicated `.local/kubeconfig`. Every mutation selects its explicit context and checks the API host is local.
5. Run `make build` to build and load the four custom images into kind. Both JavaScript builds upload matching source maps and wait for Sentry processing before this completes. Then run `make deploy` to configure Secrets/collectors, install pinned Envoy Gateway, apply storage, run role-scoped migrations and roll out the Kustomize base. `make up` runs this full sequence automatically.
6. Run `make verify-network`. Positive application paths and denied connections must both be observed. The live probes must show that this cluster enforces its NetworkPolicies; verification is required before claiming isolation.
7. Run `make down` to stop only the recorded localhost forward. `make cluster-delete` is the separate destructive command scoped to this named cluster.

The external entrypoint is `http://localhost:8080` through a loopback-only port-forward to Envoy's ClusterIP Service. PostgreSQL/Redis use `standard` PVCs. Agent checkpoint files and unsent gateway queued batches survive collector pod restarts. Persistence does not retry failed Sentry HTTP deliveries in the pinned main exporter; see [retry limits](04-collector-and-sentry.md#retry-and-persistence-limits). Application pods do not have Kubernetes API credentials.
