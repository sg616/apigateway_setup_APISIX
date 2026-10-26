# Shared helpers for gateway scripts. Source, don't execute.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../config.env
source "$ROOT/config.env"

log()  { echo "[$(date +%H:%M:%S)] $*"; }
die()  { echo "ERROR: $*" >&2; exit 1; }
need() { command -v "$1" >/dev/null || die "$1 not found in PATH"; }

# Poll URL until it returns HTTP 200 or timeout (seconds).
wait_http_200() {
  local url=$1 timeout=${2:-180} start=$SECONDS code
  while (( SECONDS - start < timeout )); do
    code=$(curl -sk -o /dev/null -w '%{http_code}' --max-time 5 "$url" || true)
    [[ $code == 200 ]] && { log "OK 200 $url"; return 0; }
    sleep 3
  done
  log "TIMEOUT waiting for 200 on $url (last: ${code:-none})"
  return 1
}
