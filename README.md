# APISIX API Gateway on sky47 CCE

sky47 has no managed API Gateway, so we run Apache APISIX in the CCE cluster.

```
Before:  Browser → ELB :443 → frontend Service (LB) → nginx ─┬─ /api/*        → backend:8080
                                                              └─ /orders-api/* → orders-backend:8080
After:   Browser → ELB :443 → apisix-gateway-lb → APISIX ─┬─ /api/*        → backend:8080
                                                          ├─ /orders-api/* → orders-backend:8080
                                                          └─ /*            → frontend-internal:80 (SPA)
```

TLS still terminates at the ELB (same cert). ELB → APISIX → pods is plain HTTP inside the VPC.

## Design

| Choice | Why |
|---|---|
| Helm chart `apisix` 2.17.1 (APISIX 3.18.0, ingress controller 2.2.0), pinned in `config.env` | Reproducible installs |
| **etcd-less**: APISIX `config_provider: yaml`, controller provider `apisix-standalone` | The cluster is small. Running 3 etcd pods would cost resources and add a backup job, without giving real HA |
| Routes as `ApisixRoute` CRDs in `routes/` | Changes go through git review, and the controller keeps the cluster in sync with what's committed |
| ELB Service kept outside Helm (`k8s/gateway-lb*.yaml`) | Cut-over and rollback never need a `helm upgrade` |
| Admin key in Secret `apisix-admin`, Admin API ClusterIP only | The key isn't in git and the Admin API isn't on the ELB |
| 2–4 replicas (HPA), PDB `minAvailable: 1`, soft anti-affinity | The gateway shouldn't be a single pod that everything depends on |

**About etcd-less mode:** each APISIX pod keeps its config in memory, and the controller pushes the full config every `syncPeriod` (10s).
A new or restarted pod stays NotReady until its first push arrives. That is expected, and readiness on `/status/ready` keeps it out of the ELB until then.
Making a change in the Admin API by hand is pointless: the next sync overwrites it.

## Layout

```
config.env                   versions, names, PUBLIC_URL
helm/values-sky47.yaml       chart values
routes/sample-app.yaml       ApisixRoute + ApisixGlobalRule (request-id, prometheus)
k8s/namespace.yaml
k8s/admin-secret.example.yaml   copy to admin-secret.yaml (gitignored)
k8s/frontend-internal.yaml      ClusterIP for the SPA (target of the /* route)
k8s/gateway-lb-test.yaml        gateway on ELB HTTPS 8443 (testing)
k8s/gateway-lb.yaml             gateway on ELB HTTPS 443 (production)
k8s/networkpolicy-apisix.yaml   Admin API only from inside the apisix namespace
k8s/networkpolicy-sample-app.yaml  backends only from gateway/frontend (hardening step)
scripts/mirror-images.sh  install.sh  smoke-test.sh  cutover.sh  rollback.sh
```

## Rollout

Run everything with the **cluster-admin (console) kubeconfig**. Installing creates CRDs and ClusterRoles, which `jenkins-deployer` is not allowed to do, and it shouldn't be.

### 0. Prerequisites
1. Install `helm` v3 (and optionally the `helm-diff` plugin) on the admin host.
2. Set `PUBLIC_URL` in `config.env`.
3. Confirm the VPC CIDR and narrow `realIpFrom` in `helm/values-sky47.yaml` if it is narrower than `10.0.0.0/8`.
4. Check the cluster's **network model** (CCE → cluster → Overview). NetworkPolicy works only with the *container tunnel* model. On *VPC network* clusters the policies are accepted but silently do nothing.
5. **LTS**: edit the CCE ingestion config and change the namespace regex from `^sample-app$` to `^(sample-app|apisix)$`, or the gateway logs never reach LTS.

### 1. Mirror images to SWR
```bash
docker login swr.pk-isb-1.sky47.com ...      # see sample project K8S-DEPLOYMENT.md §3
scripts/mirror-images.sh
```

### 2. Namespace, secrets
```bash
kubectl apply -f k8s/namespace.yaml
cp k8s/admin-secret.example.yaml k8s/admin-secret.yaml   # fill with: openssl rand -hex 32 (x2)
kubectl apply -f k8s/admin-secret.yaml
# imagePullSecret for SWR: copy default-secret from sample-app
kubectl -n sample-app get secret default-secret -o yaml \
  | sed -e 's/namespace: sample-app/namespace: apisix/' -e '/resourceVersion\|uid\|creationTimestamp/d' \
  | kubectl apply -f -
```

### 3. Install
```bash
scripts/install.sh            # helm upgrade --install --atomic, then routes + policies
kubectl -n apisix get pods,pdb,hpa
kubectl -n sample-app get apisixroute,apisixglobalrule
kubectl -n apisix logs deploy/apisix-ingress-controller -c manager | tail   # look for sync errors
```

### 4. Test on 8443 (production 443 is untouched)
```bash
kubectl apply -f k8s/gateway-lb-test.yaml
# ELB console → listener 8443 → Access Control → whitelist office/VPN IPs
scripts/smoke-test.sh https://<domain>:8443
```
Before the cut-over, also check:
- **Rate limit**: `for i in $(seq 700); do curl -sk -o /dev/null -w '%{http_code}\n' https://<domain>:8443/api/health; done | sort | uniq -c` should show 429s.
- **Logs**: JSON access lines show up in LTS with the real `client_ip`.
- **Load**: `hey -z 60s -c 20 https://<domain>:8443/api/health`. Compare p95 with the same run against `:443` (still nginx).
- **Pod loss**: delete one `apisix` pod during the load run. You should see no failures apart from in-flight requests.
- **Rollback drill**: `kubectl delete -f k8s/gateway-lb-test.yaml`, then re-apply it.

### 5. Cut-over (announced window, about 1 min outage)
```bash
scripts/cutover.sh     # backup → delete frontend LB svc → apply gateway-lb.yaml → smoke test
```
Then **straight away**, in `sample-project-sky-47-deploy-test/k8s/frontend.yaml`, delete the `frontend` LoadBalancer Service (or turn it into ClusterIP).
Otherwise the next `kubectl apply -f k8s/` brings back a Service that fights the gateway for listener 443.
Jenkins only runs `kubectl set image`, so the pipeline itself isn't affected.

If you need zero downtime, use a second ELB/EIP for `gateway-lb.yaml` and switch DNS (lower the TTL a day ahead).

### Rollback
```bash
scripts/rollback.sh    # delete gateway LB svc → re-apply only the Service from sample project frontend.yaml
```
The gateway pods and routes stay in place, so you can try again later.

### 6. Hardening (after a stable week)
- `kubectl apply -f k8s/networkpolicy-sample-app.yaml`, then check that the backend pods stay Ready (probes come from the node).
- Remove the `/api/` and `/orders-api/` blocks from `frontend/nginx.conf`, then drop the `frontend` entry from that policy.
- Update the architecture section of the sample project's `K8S-DEPLOYMENT.md`.
- Add LTS alarms on the access log: `status >= 500` rate, `request_time` p95, and the 429 rate.

## Day-2 operations

**Add a route.** Add an `http[]` entry to `routes/*.yaml`, open a PR, and once it's merged run `kubectl apply -f routes/`.
Give specific paths a `priority` above 0; the SPA catch-all is `priority: 0`.

**Upgrade.** Bump `CHART_VERSION` and the image tags in `config.env` and `values-sky47.yaml`. Read the chart and APISIX changelogs, then run `mirror-images.sh` and `install.sh`.
Helm **does not upgrade CRDs**. If the controller version changes, apply the new CRDs first:
`helm show crds apisix/apisix --version <v> | kubectl apply --server-side -f -`.

**Rotate the Admin key.** Update the `apisix-admin` Secret, then run `kubectl -n apisix rollout restart deploy/apisix deploy/apisix-ingress-controller`.

**Backup / DR.** There's no state to back up. Git (this repo plus the sample project manifests) is the whole configuration, so recovering means running `install.sh` again.

## Troubleshooting

| Symptom | Check |
|---|---|
| APISIX pods stuck NotReady | Controller logs (`-c manager`, `-c adc-server`): wrong admin key, NetworkPolicy blocking 9180, or the `apisix-config` GatewayProxy is missing |
| 404 from APISIX for a path | `kubectl -n sample-app describe apisixroute sample-app` status. Check `ingressClassName: apisix` and the path/priority |
| 502/504 | Upstream Service/endpoints exist (`kubectl -n sample-app get ep`), and the backend pod is Ready |
| `ImagePullBackOff` | Images mirrored? `default-secret` present in `apisix`? |
| Client IP is the node/ELB IP | `realIpFrom` doesn't include the ELB/node subnet |
| Listener 443 not created after cut-over | The old `frontend` LB Service still exists, or CCE events: `kubectl -n apisix describe svc apisix-gateway-lb` |
| No gateway logs in LTS | Ingestion namespace regex (prerequisite 5) |
