#!/usr/bin/env bash
# Add the OpenTelemetry Java agent to one backend Deployment, by patch (keeps the
# image Jenkins deployed). Waits for the rollout and checks the overhead gates.
# Usage: instrument.sh <backend|orders-backend>      Undo: kubectl -n sample-app rollout undo deploy/<svc>
source "$(dirname "$0")/common.sh"
need kubectl
SVC=${1:?usage: instrument.sh <backend|orders-backend>}
kubectl -n "$APP_NAMESPACE" get deploy "$SVC" >/dev/null || die "no deployment $SVC"
before_img=$(kubectl -n "$APP_NAMESPACE" get deploy "$SVC" -o jsonpath='{.spec.template.spec.containers[0].image}')

start=$SECONDS
kubectl -n "$APP_NAMESPACE" patch deploy "$SVC" --type strategic \
  -p "$(sed "s/__SERVICE__/$SVC/g" "$OBS/patches/otel-java.yaml")"
kubectl -n "$APP_NAMESPACE" annotate deploy "$SVC" kubernetes.io/change-cause="otel java agent (observability/patches)" --overwrite >/dev/null
kubectl -n "$APP_NAMESPACE" rollout status deploy "$SVC" --timeout=5m || { log "rollout failed -> undo"; kubectl -n "$APP_NAMESPACE" rollout undo deploy "$SVC"; exit 1; }
took=$((SECONDS-start))

after_img=$(kubectl -n "$APP_NAMESPACE" get deploy "$SVC" -o jsonpath='{.spec.template.spec.containers[0].image}')
[[ $before_img == "$after_img" ]] || die "image changed ($before_img -> $after_img)?!"
# newest pod (an old one can still be Running while it terminates)
pod=$(kubectl -n "$APP_NAMESPACE" get pod -l app="$SVC" --field-selector=status.phase=Running \
  --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1:].metadata.name}')
restarts=$(kubectl -n "$APP_NAMESPACE" get pod "$pod" -o jsonpath='{.status.containerStatuses[0].restartCount}')
agent=$(kubectl -n "$APP_NAMESPACE" logs "$pod" -c "$SVC" | grep -m1 -i "opentelemetry-javaagent" || true)
log "$SVC: rollout ${took}s, restarts=$restarts, image unchanged"
log "agent: ${agent:-NOT FOUND in logs}"
(( took < 300 )) && [[ $restarts == 0 && -n $agent ]] || { log "gate failed -> undo"; kubectl -n "$APP_NAMESPACE" rollout undo deploy "$SVC"; exit 1; }
