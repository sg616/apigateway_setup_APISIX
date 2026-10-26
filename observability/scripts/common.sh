# Shared setup for observability scripts. Source, don't execute.
# shellcheck source=../../scripts/lib.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../scripts" && pwd)/lib.sh"   # log/die/need, gateway config.env
OBS="$ROOT/observability"
# shellcheck source=../config.env
source "$OBS/config.env"

# Run a command inside the cluster (from the app's frontend pod, which has curl).
# Read-only use only: never write files in that pod.
incluster() { kubectl -n "$APP_NAMESPACE" exec deploy/frontend -- "$@"; }
