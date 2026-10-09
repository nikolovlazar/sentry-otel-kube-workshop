# Public readiness

## Decisions

MIT, placeholder configuration, locked dependency versions and image digests; synthetic data only. No remote publication.

## Steps

Audit tracked files and Docker contexts; inspect ignored local state without exposing values; run checks; document limitations and current version matrix.

## Acceptance

No management/upload tokens or passwords in tracked files or images; committed examples use placeholder DSNs. The browser DSN is a public ingest key and can legitimately appear in frontend assets; docs distinguish live verification from pending work; repository is ready for later public review.

## Completion checklist

- [x] Implementation complete
- [x] Automated checks complete
- [x] Live verification recorded where applicable
- [x] Documentation matches observed behavior

## Prerequisites and commands

Use the current Git checkout, installed JavaScript dependencies and completed application/telemetry acceptance evidence. Run these checks from the repository root:

```sh
git status --short
git diff --check
git ls-files .env .local/
git check-ignore .env .local/
git remote -v
PATH="$PWD/.local/bin:$PATH" npm --prefix apps/web audit --json
PATH="$PWD/.local/bin:$PATH" npm --prefix services/notification-worker audit --json
```

Expect no tracked private-state paths, working ignore rules, no configured remote, no whitespace failures and zero dependency vulnerabilities. The private value/image comparisons in the review procedure remain necessary; a textual pattern scan alone cannot prove that built artifacts contain no credentials.

## Audit

No management/upload token or password in tracked source, examples, lockfiles or Docker build contexts. Actual project DSNs stay in local configuration; committed examples use placeholders. The browser DSN is a public ingest key, so its presence in served frontend configuration or assets is expected. .env and .local are ignored; management/upload credentials stay server/local. Token flags are never included in logged commands.

Commit dependency locks and exact image/tool provenance. Use synthetic data and parameterized queries; preserve readable source. Verify the README against observed commands. Document beta/alpha/metrics and single-consumer boundaries. Keep local event evidence private and public examples sanitized.

No public GitHub repository is created in this scope.

## Review procedure

1. Check `git status`, tracked paths and ignore rules. `.env`, `.local`, language caches, compiled output and source maps generated during builds must be ignored.
2. Compare tracked text against actual local secret values without printing those values; scan for DSNs, token prefixes, credentials, personal absolute paths and corporate registry URLs. Public locks retain canonical registry URLs and unchanged integrity hashes.
3. Inspect Dockerfiles, build contexts and image metadata. Runtime credentials enter only through named Kubernetes Secret keys. JavaScript upload tokens enter the build only through a BuildKit secret and remain absent from image arguments, environment variables, layers and runtime Secrets. Base images, infrastructure images and downloaded tools are pinned/checksummed.
4. Run the documented commands against current source/images. Save public summaries and private detailed evidence separately.
5. Commit the local repository with the MIT license and documentation. No remote is configured and no GitHub repository is created.

The README distinguishes supported local behavior from future hosted deployment. The collector's limited token and source-map upload token have separate ownership. Browser ingest keys are public by design, but examples remain placeholders. The runtime uses synthetic data and never sends external notifications.
