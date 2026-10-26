#!/usr/bin/env bash
# Install or upgrade the gateway + routes. Idempotent.
# Run with a CLUSTER-ADMIN kubeconfig (installs CRDs/ClusterRoles), not the
# namespace-scoped jenkins-deployer one.
source "$(dirname "$0")/lib.sh"
need kubectl; need helm

kubectl apply -f "$ROOT/k8s/namespace.yaml"
kubectl -n "$NAMESPACE" get secret apisix-admin >/dev/null 2>&1 \
  || die "Secret apisix-admin missing: create it from k8s/admin-secret.example.yaml first"
kubectl -n "$NAMESPACE" get secret default-secret >/dev/null 2>&1 \
  || die "imagePullSecret default-secret missing in $NAMESPACE (copy it from $APP_NAMESPACE)"

helm repo add apisix "$CHART_REPO" >/dev/null 2>&1 || true
helm repo update apisix >/dev/null

args=(upgrade --install "$RELEASE" apisix/apisix --version "$CHART_VERSION"
      -n "$NAMESPACE" -f "$ROOT/helm/values-sky47.yaml")
if helm plugin list 2>/dev/null | grep -q '^diff'; then
  helm diff "${args[@]:1}" || true
  [[ ${YES:-} == 1 ]] || { read -rp "Apply this diff? [y/N] " a; [[ $a == y ]] || die "aborted"; }
fi
helm "${args[@]}" --atomic --timeout 5m

kubectl -n "$NAMESPACE" rollout status deploy/"$RELEASE" --timeout=3m
kubectl apply -f "$ROOT/k8s/networkpolicy-apisix.yaml"
kubectl apply -f "$ROOT/k8s/frontend-internal.yaml"
kubectl apply -f "$ROOT/routes/"
log "installed. Next: kubectl apply -f k8s/gateway-lb-test.yaml && scripts/smoke-test.sh https://<elb-host>:8443"
