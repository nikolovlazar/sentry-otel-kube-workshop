# Verified versions and provenance

Verified on 2026-10-09. Runtime manifests pin published image digests; local application images use `:local` and are rebuilt and loaded together. Language dependency versions are locked in each application's package lock, `uv.lock`, or `go.sum`.

| Component | Pin | Primary source |
| --- | --- | --- |
| kind | 0.33.0 | [release and node digests](https://github.com/kubernetes-sigs/kind/releases/tag/v0.33.0) |
| Kubernetes / kubectl | 1.36.4 | [macOS installation/checksums](https://kubernetes.io/docs/tasks/tools/install-kubectl-macos/) |
| Helm | 4.3.0 | [release](https://github.com/helm/helm/releases/tag/v4.3.0), [Kubernetes compatibility](https://helm.sh/docs/topics/version_skew/) |
| Local Node.js | 24.21.0 | [release and SHA256 checksums](https://nodejs.org/dist/v24.21.0/SHASUMS256.txt) |
| Envoy Gateway chart | 1.9.2 | [quickstart](https://gateway.envoyproxy.io/docs/tasks/quickstart/), [compatibility matrix](https://gateway.envoyproxy.io/news/releases/matrix/) |
| Envoy data plane | distroless-v1.39.1 | Default compatible data-plane image from the pinned Envoy Gateway release |
| Collector Contrib | 0.162.0 | [release](https://github.com/open-telemetry/opentelemetry-collector-contrib/releases/tag/v0.162.0) |
| PostgreSQL | 17.11 | [official image](https://hub.docker.com/_/postgres) |
| Redis | 7.4.11 | [official image](https://hub.docker.com/_/redis) |
| Network probe | BusyBox 1.37.0 | [official image](https://hub.docker.com/_/busybox) |
| Source-map upload CLI | @sentry/cli 3.8.0 | Development dependency in both JavaScript packages, locked in their `package-lock.json` files |

Bootstrap downloads the exact kind, kubectl, Helm and Node release artifacts for macOS/Linux arm64/amd64 and verifies their publisher-hosted SHA256 checksums before installation into ignored `.local/bin`. It does not update global tools. The installed Sentry CLI may coexist with the project-local SDK CLI; source-map uploads use the locked SDK CLI to avoid global-version differences.

## Immutable runtime artifacts

- kind node: `kindest/node:v1.36.4@sha256:099e049362a1526b2db71494e1947aae99bd16290d7c895f2b7ea312e3cbfaed`
- Collector: `otel/opentelemetry-collector-contrib:0.162.0@sha256:39923a8e431bd1f57be82411999d389fcfe40857492e4365456d97a4c1f74be6`
- PostgreSQL: `postgres:17.11@sha256:2d2b8998d31037bf721cfdf764d76ba74171b4fab3431b7f72c27c56ddbdf9e3`
- Redis: `redis:7.4.11@sha256:4fa24486b8bcca8eec45ee0eb166edc674795e53a2b53d1a9ef263eecebaac85`
- Envoy controller: `docker.io/envoyproxy/gateway:v1.9.2@sha256:9d67017c442a70e6ae9f1a358e6c80eb653c67c2e40823d9a991e0daf37ba926`
- Envoy proxy: `docker.io/envoyproxy/envoy:distroless-v1.39.1@sha256:eb2c01c13125d1629637cb4e4cce7207009fb7cc2c8027f9742758549d15b6f4`
- BusyBox: `busybox:1.37.0@sha256:bdf57e528e45e4433820e045b29b4597825a1c9e38353532d90a01445013f82e`
- Envoy chart OCI digest: `sha256:be034275b55deeddd6b7bc1f4da6eeb02b8efc418a4a29e2b1977851b24b1e63`
- Downloaded chart archive SHA256: `1079cad009e0885f6e10e5f712257d8e5fdaf911d2ceb3fa1b78632c8f29bbf9`

The cluster uses Kubernetes 1.36 rather than kind's 1.37 default because released Envoy Gateway 1.9 explicitly supports 1.33–1.36. Helm 4.3 supports 1.34–1.37. Do not change only one component's version without repeating the compatibility and live verification checks.

## Local resource budget

The attendee baseline is one kind node and one replica per custom service. Give the container runtime 4 CPUs, 8 GiB memory and 20 GiB free disk; 16 GiB host memory is recommended. The live presenter runtime allocation was 7 CPUs and 18 GiB memory. That allocation was exercised; 4 CPUs/8 GiB is the lower sizing requirement and has not been separately benchmarked. Builds run through the host runtime rather than as cluster Jobs.

The four applications, storage, collectors, Envoy proxy and controller request about 1.5 GiB of memory and have combined 3.25 GiB primary-container limits. The generated shutdown-manager helper adds a 32 MiB request. Allow additional room for two small migration Jobs and Kubernetes system components. Next.js/worker run as numeric UID1000. The node log agent runs as root with dropped capabilities, no privilege escalation, read-only pod-log access and a dedicated state directory; this is the explicit exception needed to read node log files.

PostgreSQL and Redis use `standard` local-path PVCs. Ordinary deploys, browser-forwarding shutdown and resets preserve PVCs. Explicit named-cluster deletion removes the cluster's local volumes. This local storage demonstrates stateful workloads; it does not provide production replication or backup guarantees.

## Application build bases

These multi-platform official image digests are pinned in the four Dockerfiles.

- `node:24.21.0-bookworm-slim@sha256:d6aa754f16b3197301076f047b5def2f02ea1dbbc2ca920407d46d7ec7f87b20`
- `python:3.13-slim@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f`
- `golang:1.25.6-alpine@sha256:98e6cffc31ccc44c7c15d83df1d69891efee8115a5bb7ede2bf30a38af3e3c92`
- `alpine:3.22@sha256:5291449c3df73caf6ed85e649dec1b9e818b39a5d8c871e97afc13e9cd5e8fa8`
- `ghcr.io/astral-sh/uv:0.9.4@sha256:c4089b0085cf4d38e38d5cdaa5e57752c1878a6f41f2e3a3a234dc5f23942cb4`

The deployment entrypoint is `infra/kubernetes/kustomization.yaml`; the orchestration script applies this base with the explicitly scoped kubeconfig after provisioning Secrets, storage and role-specific migrations. `SERVICE_VERSION` defaults to the current Git commit SHA (or `dev-uncommitted` before the first commit), and `SENTRY_RELEASE` defaults to `sentry-otel-workshop@` plus that version. Rebuild and redeploy after committing to keep runtime resources, errors and uploaded source maps on the same release.

## Language and SDK compatibility set

| Workload | Runtime/framework | Sentry | OTel SDK/exporter |
| --- | --- | --- | --- |
| Web | Node24.21.0, Next16.4.0, React19.3.0, TS5.9.3 | @sentry/nextjs11.6.0 | NodeSDK0.223.0, stable packages2.12.0, API1.9.1 |
| Catalog | Python3.13, FastAPI0.143.0, Uvicorn0.54.0, Pydantic2.12.3 | sentry-sdk2.71.0 | 1.45.1 / instrumentation0.66b1 |
| Fulfillment | Go1.25.6, grpc1.83.0, pgx5.8.0, go-redis9.17.3 | sentry-go/otel0.50.0 | 1.45.0 |
| Worker | Node24.21.0, TS5.9.3, Redis client5.8.2 | @sentry/node11.6.0 | Same Node stack as web |

The JavaScript gRPC client is pinned to1.14.6 after auditing1.14.3 revealed current high-severity advisories; both final dependency audits reported zero vulnerabilities. The catalog intentionally uses the HTTP-only OTel exporter; Sentry's broad optional OTLP extra includes an unused gRPC exporter. Public lockfiles keep canonical registry URLs and unchanged artifact hashes. Local approved registry mirrors remain usable through package-manager configuration.

The Envoy shutdown helper adds a32MiB memory request. The primary-container totals exclude this helper, transient migrations and system components; the helper uses the pinned Envoy Gateway image. Live resource usage is recorded during rehearsal.

## Final local verification snapshot

Application release: `6f4c13254eb81e0f1e40ccfe7776639963a491e9`. The presenter's ignored local configuration freezes this verified release while later commits finish documentation, verification and reset orchestration. Fresh checkouts use their current Git SHA by default. Remove/update both `SERVICE_VERSION` and `SENTRY_RELEASE` overrides before changing application source, then build/deploy and verify again. See the [verification record](verification.md) for final rehearsal results and the tested resource allocation.
