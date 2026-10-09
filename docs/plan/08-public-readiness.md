# Public readiness

## Decisions

MIT, placeholder configuration, locked dependency versions and image digests; synthetic data only. No remote publication.

## Steps

Audit tracked files and Docker contexts; inspect ignored local state without exposing values; run checks; document limitations and current version matrix.

## Acceptance

No actual tokens/DSNs/passwords in tracked files or images; docs distinguish live verification from pending work; repository is ready for later public review.

## Completion checklist

- [ ] Implementation complete
- [ ] Automated checks complete
- [ ] Live verification recorded where applicable
- [ ] Documentation matches observed behavior

## Audit

No actual token/password/DSN in tracked source, examples, lockfiles or Docker build contexts. .env and .local are ignored. Runtime browser DSN is a public ingest key; management/upload credentials stay server/local. Token flags are never included in logged commands.

Commit dependency locks and exact image/tool provenance. Use synthetic data and parameterized queries; preserve readable source. Verify the README against observed commands. Document beta/alpha/metrics and single-consumer boundaries. Keep local event evidence private and public examples sanitized.

No public GitHub repository is created in this scope.

## Review procedure

1. Check `git status`, tracked paths and ignore rules. `.env`, `.local`, language caches, compiled output and source maps generated during builds must be ignored.
2. Compare tracked text against actual local secret values without printing those values; scan for DSNs, token prefixes, credentials, personal absolute paths and corporate registry URLs. Public locks retain canonical registry URLs and unchanged integrity hashes.
3. Inspect Dockerfiles, build contexts and image metadata. Runtime credentials enter only through named Kubernetes Secret keys. Base images, infrastructure images and downloaded tools are pinned/checksummed.
4. Run the documented commands against current source/images. Save public summaries and private detailed evidence separately.
5. Commit the local repository with the MIT license and documentation. No remote is configured and no GitHub repository is created.

The README distinguishes supported local behavior from future hosted deployment. The collector's limited token and source-map upload token have separate ownership. Browser ingest keys are public by design, but examples remain placeholders. The runtime uses synthetic data and never sends external notifications.
