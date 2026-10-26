#!/usr/bin/env bash
# Return public 443 to the frontend nginx Service (pre-gateway state).
# Usage: rollback.sh [FRONTEND_SVC_YAML]   (default: k8s/frontend-lb-rollback.yaml)
# Only a Service is applied, so Deployments (image tags managed by Jenkins) are untouched.
source "$(dirname "$0")/lib.sh"
need kubectl
SRC=${1:-$ROOT/k8s/frontend-lb-rollback.yaml}
[[ -f $SRC ]] || die "frontend Service manifest not found: $SRC"

log "step 1: release 443 from the gateway"
kubectl -n "$NAMESPACE" delete svc apisix-gateway-lb --ignore-not-found --wait=true
sleep 15

log "step 2: restore frontend LoadBalancer Service from $SRC"
kubectl apply -f "$SRC"

[[ $PUBLIC_URL == *CHANGE-ME* ]] || wait_http_200 "$PUBLIC_URL/api/health" 240
log "rolled back. Gateway pods/routes are untouched; re-test via k8s/gateway-lb-test.yaml."
log "Before cutting over again: kubectl -n $APP_NAMESPACE delete svc frontend"
