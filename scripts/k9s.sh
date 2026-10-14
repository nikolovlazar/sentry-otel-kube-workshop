#!/usr/bin/env bash
set -euo pipefail

# Resolve the checkout even when launched through the installed symlink.
source_path="${BASH_SOURCE[0]}"
while [[ -L "$source_path" ]]; do
  source_dir="$(cd -P "$(dirname "$source_path")" && pwd)"
  source_path="$(readlink "$source_path")"
  [[ "$source_path" = /* ]] || source_path="$source_dir/$source_path"
done
project_dir="$(cd -P "$(dirname "$source_path")/.." && pwd)"
if ! command -v k9s >/dev/null 2>&1; then
  echo 'K9s is missing. Install with: brew install derailed/k9s/k9s' >&2
  exit 1
fi
if [[ ! -f "$project_dir/.local/kubeconfig" ]]; then
  echo "Workshop kubeconfig is missing. Run make cluster-up in $project_dir." >&2
  exit 1
fi
export KUBECONFIG="$project_dir/.local/kubeconfig"
export PATH="$project_dir/.local/bin:$PATH"
exec k9s "$@" --context kind-sentry-otel-workshop --namespace sentry-otel-workshop
