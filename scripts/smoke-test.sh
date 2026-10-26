#!/usr/bin/env bash
# Functional checks through the gateway. Usage: smoke-test.sh [BASE_URL]
# BASE_URL defaults to PUBLIC_URL from config.env, e.g. https://host:8443 for the test listener.
source "$(dirname "$0")/lib.sh"
BASE=${1:-$PUBLIC_URL}
fail=0
check() {  # path expected_code [header]
  local out code
  out=$(curl -sk -D - -o /dev/null --max-time 10 "$BASE$1" || true)
  code=$(awk 'NR==1{print $2}' <<<"$out")
  if [[ $code == "$2" ]]; then log "PASS $1 -> $code"; else log "FAIL $1 -> ${code:-none} (want $2)"; fail=1; fi
  if [[ -n ${3:-} ]] && ! grep -qi "^$3:" <<<"$out"; then log "FAIL $1 missing header $3"; fail=1; fi
}
check /api/health 200 X-Request-Id
check /orders-api/health 200 X-Request-Id
check / 200
check /some/spa/route 200          # SPA fallback via nginx try_files
exit $fail
