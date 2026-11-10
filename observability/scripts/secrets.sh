#!/usr/bin/env bash
# Grafana admin login, random, created in-cluster only. Never overwrites, never prints.
# Read it yourself when you need it:
#   kubectl -n observability get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d; echo
source "$(dirname "$0")/common.sh"
need kubectl; need openssl
kubectl apply -f "$OBS/k8s/namespace.yaml" >/dev/null
if kubectl -n "$OBS_NAMESPACE" get secret grafana-admin >/dev/null 2>&1; then
  log "secret grafana-admin exists, kept"
else
  kubectl -n "$OBS_NAMESPACE" create secret generic grafana-admin \
    --from-literal=admin-user=admin \
    --from-literal=admin-password="$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-24)" >/dev/null
  log "secret grafana-admin created"
fi
# CCE creates default-secret (SWR pull) in new namespaces by itself; copy if not.
if ! kubectl -n "$OBS_NAMESPACE" get secret default-secret >/dev/null 2>&1; then
  kubectl -n "$APP_NAMESPACE" get secret default-secret -o json | python3 -c '
import json,sys
s=json.load(sys.stdin)
print(json.dumps({"apiVersion":"v1","kind":"Secret","type":s["type"],
  "metadata":{"name":"default-secret","namespace":sys.argv[1]},"data":s["data"]}))' "$OBS_NAMESPACE" | kubectl apply -f -
  log "secret default-secret copied from $APP_NAMESPACE"
fi
