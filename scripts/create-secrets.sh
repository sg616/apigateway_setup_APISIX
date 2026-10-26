#!/usr/bin/env bash
# Create the namespace and the secrets the install needs. Idempotent: never
# overwrites an existing secret. The admin keys are random, live only in the
# cluster, and are not printed.
source "$(dirname "$0")/lib.sh"
need kubectl; need openssl
kubectl apply -f "$ROOT/k8s/namespace.yaml"

if kubectl -n "$NAMESPACE" get secret apisix-admin >/dev/null 2>&1; then
  log "secret apisix-admin exists, kept"
else
  kubectl -n "$NAMESPACE" create secret generic apisix-admin \
    --from-literal=admin-key="$(openssl rand -hex 32)" \
    --from-literal=viewer-key="$(openssl rand -hex 32)" >/dev/null
  log "secret apisix-admin created"
fi

# CCE normally creates default-secret in every new namespace by itself.
if kubectl -n "$NAMESPACE" get secret default-secret >/dev/null 2>&1; then
  log "secret default-secret exists, kept"
else
  kubectl -n "$APP_NAMESPACE" get secret default-secret -o json | python3 -c '
import json,sys
s=json.load(sys.stdin)
print(json.dumps({"apiVersion":"v1","kind":"Secret","type":s["type"],
  "metadata":{"name":"default-secret","namespace":sys.argv[1]},"data":s["data"]}))' "$NAMESPACE" \
    | kubectl apply -f -
  log "secret default-secret copied from $APP_NAMESPACE"
fi
