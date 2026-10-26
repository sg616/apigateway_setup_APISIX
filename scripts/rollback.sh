#!/usr/bin/env bash
# Return public 443 to the frontend nginx Service (pre-gateway state).
# Usage: rollback.sh [FRONTEND_MANIFEST]
# Default: the sample project's k8s/frontend.yaml. Only its Service document is
# applied, so the Deployment (image tag managed by Jenkins) is not touched.
source "$(dirname "$0")/lib.sh"
need kubectl
SRC=${1:-$ROOT/../sample-project-sky-47-deploy-test/k8s/frontend.yaml}
[[ -f $SRC ]] || die "frontend manifest not found: $SRC"

log "step 1: release 443 from the gateway"
kubectl -n "$NAMESPACE" delete svc apisix-gateway-lb --ignore-not-found --wait=true
sleep 15

log "step 2: restore frontend Service from $SRC"
awk 'BEGIN{RS="\n---\n"} /\nkind: Service\n/ {print; exit}' "$SRC" | kubectl apply -f -

[[ $PUBLIC_URL == *CHANGE-ME* ]] || wait_http_200 "$PUBLIC_URL/api/health" 240
log "rolled back. Gateway pods/routes are untouched; re-test via k8s/gateway-lb-test.yaml."
