#!/usr/bin/env bash
# Move public HTTPS 443 from the frontend Service to the APISIX gateway.
# Expect < 1 min of downtime between the two steps. Run in an announced window.
#   1. delete frontend LoadBalancer Service  -> CCE removes the 443 listener
#   2. apply k8s/gateway-lb.yaml             -> CCE creates 443 -> gateway
# Rollback: scripts/rollback.sh
source "$(dirname "$0")/lib.sh"
need kubectl; need curl
[[ $PUBLIC_URL != *CHANGE-ME* ]] || die "set PUBLIC_URL in config.env"

BACKUP="$ROOT/backup/frontend-svc-$(date +%Y%m%d-%H%M%S).yaml"
mkdir -p "$ROOT/backup"
kubectl -n "$APP_NAMESPACE" get svc frontend -o yaml > "$BACKUP"
log "backed up frontend Service to $BACKUP"

log "pre-check: gateway healthy and routes synced"
kubectl -n "$NAMESPACE" rollout status deploy/"$RELEASE" --timeout=60s
kubectl -n "$APP_NAMESPACE" get endpoints frontend-internal -o jsonpath='{.subsets[0].addresses[0].ip}' | grep -q . \
  || die "frontend-internal has no endpoints"

[[ ${YES:-} == 1 ]] || { read -rp "Cut over $PUBLIC_URL to APISIX now? [y/N] " a; [[ $a == y ]] || die "aborted"; }

log "step 1: delete test listener + frontend LoadBalancer Service"
kubectl -n "$NAMESPACE" delete svc apisix-gateway-lb --ignore-not-found --wait=true
kubectl -n "$APP_NAMESPACE" delete svc frontend --wait=true
sleep 15   # give CCE time to remove the 443 listener

log "step 2: gateway takes 443"
kubectl apply -f "$ROOT/k8s/gateway-lb.yaml"

if wait_http_200 "$PUBLIC_URL/api/health" 240 && "$ROOT/scripts/smoke-test.sh" "$PUBLIC_URL"; then
  log "CUT-OVER DONE."
  log "NOW: update sample-project k8s/frontend.yaml (drop the LoadBalancer Service) or a"
  log "     future 'kubectl apply -f k8s/' will fight the gateway for listener 443."
else
  log "smoke test failed -> run scripts/rollback.sh"
  exit 1
fi
