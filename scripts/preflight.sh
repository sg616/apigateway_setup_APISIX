#!/usr/bin/env bash
# Read-only checks before installing in a cluster. Prints the values that
# have to go into config.env / helm/values-sky47.yaml / k8s/gateway-lb*.yaml.
# Changes nothing. Run with the cluster-admin kubeconfig.
source "$(dirname "$0")/lib.sh"
set +e
need kubectl
ok()   { echo "  [ok]   $*"; }
warn() { echo "  [WARN] $*"; }
yq_ann() { grep -m1 "$1" "$2" | sed 's/.*: *"\{0,1\}\([^"]*\)"\{0,1\}$/\1/'; }

echo "== cluster"
kubectl version 2>/dev/null | tail -1 | sed 's/^/  /'
[[ $(kubectl auth can-i '*' '*' --all-namespaces 2>/dev/null) == yes ]] && ok "cluster-admin" || warn "not cluster-admin: install needs CRDs/ClusterRoles"
command -v helm >/dev/null && ok "helm $(helm version --short)" || warn "helm not in PATH (any v3; no sudo needed, a copy in ~/bin is fine)"

echo "== nodes"
kubectl get nodes -o custom-columns='  NAME:.metadata.name,ARCH:.status.nodeInfo.architecture,CPU:.status.allocatable.cpu,MEM:.status.allocatable.memory' 2>/dev/null
n=$(kubectl get nodes --no-headers 2>/dev/null | wc -l); (( n < 2 )) && warn "$n node: both APISIX replicas share it (anti-affinity is soft)"
grep -q amd64 <<<"$(kubectl get nodes -o jsonpath='{.items[*].status.nodeInfo.architecture}')" || warn "non-amd64 node: mirror-images.sh pushes the host platform only"

echo "== networks (for apisix.nginx.http.realIpFrom)"
node_ip=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="InternalIP")].address}')
pod_ip=$(kubectl -n kube-system get pods -o jsonpath='{range .items[*]}{.status.podIP}{"\n"}{end}' | grep -v "^$node_ip$" | grep -m1 .)
echo "  node IP: $node_ip -> VPC range likely ${node_ip%.*.*}.0.0/16"
echo "  pod IP:  ${pod_ip:-?} -> container range likely ${pod_ip%.*.*}.0.0/16"
echo "  values file has: $(grep -A4 'realIpFrom:' "$ROOT/helm/values-sky47.yaml" | grep -- '- ' | tr -d ' -' | tr '\n' ' ')"
if kubectl -n kube-system get ds 2>/dev/null | grep -qiE 'canal|calico|yangtse|cilium'; then ok "policy-capable CNI found"; else warn "no canal/calico/yangtse DaemonSet: NetworkPolicy is probably NOT enforced"; fi

echo "== app namespace $APP_NAMESPACE"
for d in backend orders-backend frontend; do kubectl -n "$APP_NAMESPACE" get deploy "$d" >/dev/null 2>&1 && ok "deployment $d" || warn "deployment $d missing (routes would 503)"; done
for s in backend orders-backend; do kubectl -n "$APP_NAMESPACE" get svc "$s" >/dev/null 2>&1 && ok "service $s" || warn "service $s missing"; done
kubectl -n "$APP_NAMESPACE" get secret default-secret >/dev/null 2>&1 && ok "default-secret (SWR pull secret)" || warn "default-secret missing in $APP_NAMESPACE"

echo "== ELB binding"
repo_elb=$(yq_ann 'elb.id' "$ROOT/k8s/gateway-lb.yaml"); repo_cert=$(yq_ann 'elb.cert-id' "$ROOT/k8s/gateway-lb.yaml")
echo "  repo gateway-lb.yaml: elb.id=$repo_elb cert-id=$repo_cert"
kubectl get svc -A -o jsonpath='{range .items[?(@.spec.type=="LoadBalancer")]}{.metadata.namespace}/{.metadata.name} ports={.spec.ports[*].port} elb.id={.metadata.annotations.kubernetes\.io/elb\.id} cert-id={.metadata.annotations.kubernetes\.io/elb\.cert-id}{"\n"}{end}' \
  | sed 's/^/  live: /'
kubectl get svc -A -o jsonpath='{range .items[?(@.spec.type=="LoadBalancer")]}{.metadata.annotations.kubernetes\.io/elb\.id}{"\n"}{end}' | grep -q "$repo_elb" \
  && ok "repo elb.id matches a live LoadBalancer Service" || warn "repo elb.id not seen on any live Service: set it (ELB console -> ID) in gateway-lb*.yaml and frontend-lb-rollback.yaml"

echo "== monitoring"
kubectl get crd servicemonitors.monitoring.coreos.com >/dev/null 2>&1 && ok "ServiceMonitor CRD (metrics.serviceMonitor.enabled: true works)" || warn "no ServiceMonitor CRD: set metrics.serviceMonitor.enabled: false in values"
kubectl top nodes >/dev/null 2>&1 && ok "metrics API (HPA can scale)" || warn "no metrics API: HPA shows cpu <unknown> and stays at minReplicas"

echo "== existing install"
kubectl get ns "$NAMESPACE" >/dev/null 2>&1 && echo "  namespace $NAMESPACE exists: $(kubectl -n "$NAMESPACE" get pods --no-headers 2>/dev/null | wc -l) pods" || echo "  namespace $NAMESPACE not present (fresh install)"

echo "== public URL"
if [[ $PUBLIC_URL == *CHANGE-ME* ]]; then warn "set PUBLIC_URL in config.env"; else
  for p in / /api/health /orders-api/health; do echo "  $p -> $(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$PUBLIC_URL$p")"; done; fi
