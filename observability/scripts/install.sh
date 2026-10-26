#!/usr/bin/env bash
# Install/upgrade the observability stack (Prometheus, Loki, Tempo, Alloy, Grafana)
# and the provisioned dashboards. Idempotent. Run with the cluster-admin kubeconfig.
# Does NOT touch the gateway or the app: see README for those steps.
source "$(dirname "$0")/common.sh"
need kubectl; need helm
kubectl apply -f "$OBS/k8s/"
kubectl -n "$OBS_NAMESPACE" get secret grafana-admin >/dev/null 2>&1 || die "run observability/scripts/secrets.sh first"
kubectl -n "$OBS_NAMESPACE" get secret default-secret >/dev/null 2>&1 || die "default-secret missing: run observability/scripts/secrets.sh"

helm repo add grafana-community "$GC_REPO" >/dev/null 2>&1 || true
helm repo add grafana "$GRAFANA_REPO" >/dev/null 2>&1 || true
helm repo add prometheus-community "$PC_REPO" >/dev/null 2>&1 || true
helm repo update grafana-community grafana prometheus-community >/dev/null

# Dashboards: one ConfigMap, mounted read-only by Grafana (allowUiUpdates: false)
kubectl -n "$OBS_NAMESPACE" create configmap grafana-dashboards-gateway \
  --from-file="$OBS/dashboards/" --dry-run=client -o yaml | kubectl apply -f -

up() {  # release chart version
  log "helm $1 ($2 $3)"
  helm upgrade --install "$1" "$2" --version "$3" -n "$OBS_NAMESPACE" \
    -f "$OBS/values/$1.yaml" --atomic --timeout 10m
}
up prometheus prometheus-community/prometheus "$PROMETHEUS_CHART"
up loki       grafana-community/loki          "$LOKI_CHART"
up tempo      grafana-community/tempo         "$TEMPO_CHART"
up alloy      grafana/alloy                   "$ALLOY_CHART"
up grafana    grafana-community/grafana       "$GRAFANA_CHART"
# Grafana only reads dashboards at start; restart if the ConfigMap changed
kubectl -n "$OBS_NAMESPACE" rollout restart deploy/grafana >/dev/null
kubectl -n "$OBS_NAMESPACE" rollout status deploy/grafana --timeout=3m

kubectl -n "$OBS_NAMESPACE" get pods,pvc
log "stack installed. Next: gateway tracing (README step 4), then instrument.sh, then the Grafana route."
