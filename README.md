# APISIX API Gateway on sky47 CCE

sky47 has no managed API Gateway, so we run Apache APISIX in the CCE cluster.
This repo is everything needed to set it up again on a new cluster by hand. Deployment records are in [`docs/deployments/`](docs/deployments/).

```
Browser → ELB :443 (TLS) → apisix-gateway-lb (ns apisix) → APISIX ─┬─ /api/*        → backend:8080
                                                                    ├─ /orders-api/* → orders-backend:8080
                                                                    └─ /*            → frontend-internal:80 (SPA)
```

TLS terminates at the ELB. ELB → APISIX → pods is plain HTTP inside the VPC.
The app (Deployments + backend Services) comes from the app repo [sample-project-sky-47-deploy-test](https://github.com/sg616/sample-project-sky-47-deploy-test).
Its `k8s/frontend.yaml` has **no** LoadBalancer Service (branch `feature/apisix-gateway`), because the ELB belongs to this repo.

## Design

| Choice | Why |
|---|---|
| Helm chart `apisix` 2.17.1 (APISIX 3.18.0, ingress controller 2.2.0), pinned in `config.env` | Reproducible installs |
| **etcd-less**: APISIX `config_provider: yaml`, controller provider `apisix-standalone` | Small clusters. 3 etcd pods would cost resources and add a backup job, without giving real HA |
| Routes as `ApisixRoute` CRDs in `routes/` | Changes go through git review, and the controller keeps the cluster in sync with what's committed |
| ELB Service kept outside Helm (`k8s/gateway-lb*.yaml`) | Cut-over and rollback never need a `helm upgrade` |
| Admin key in Secret `apisix-admin` (random, created in-cluster), Admin API ClusterIP only | The key is never in git or on disk, and the Admin API isn't on the ELB |
| 2–4 replicas (HPA), PDB `minAvailable: 1`, soft anti-affinity | The gateway shouldn't be a single pod that everything depends on |
| JSON access log on stdout, ServiceMonitor | Logs go to LTS through ICAgent, metrics to AOM through the CCE Prometheus agent |

**About etcd-less mode:** each APISIX pod keeps its config in memory, and the controller pushes the full config every `syncPeriod` (10s).
A new or restarted pod stays NotReady until its first push arrives. That is expected, and readiness on `/status/ready` keeps it out of the ELB until then.
Making a change in the Admin API by hand is pointless: the next sync overwrites it.

## Layout

```
config.env                         versions, names, SWR path, PUBLIC_URL
helm/values-sky47.yaml             chart values
routes/sample-app.yaml             ApisixRoute (3 routes, rate limit) + ApisixGlobalRule (request-id, prometheus)
k8s/namespace.yaml
k8s/frontend-internal.yaml         ClusterIP for the SPA (target of the /* route)
k8s/gateway-lb-test.yaml           gateway on ELB HTTPS 8443 (testing)
k8s/gateway-lb.yaml                gateway on ELB HTTPS 443 (production)
k8s/frontend-lb-rollback.yaml      pre-gateway frontend LoadBalancer Service (rollback only)
k8s/networkpolicy-apisix.yaml      Admin API only from inside the apisix namespace
k8s/networkpolicy-sample-app.yaml  backends only from gateway/frontend (hardening step)
scripts/preflight.sh               read-only checks; prints the values a new cluster needs
scripts/swr-login.sh               long-term SWR login from AK/SK (password on stdin)
scripts/mirror-images.sh           Docker Hub / ghcr -> SWR
scripts/create-secrets.sh          namespace + admin key + pull secret (idempotent)
scripts/install.sh                 helm upgrade --install --atomic, routes, policies
scripts/smoke-test.sh              4 HTTP checks through the gateway
scripts/cutover.sh / rollback.sh   move public 443 to APISIX / back to nginx
docs/deployments/                  one record per environment
docs/lts-alarms.md                 LTS (and AOM) alarm rules for the gateway
```

## Values per environment

For a new cluster or ELB, change these and nothing else. `scripts/preflight.sh` prints the live values to compare against.

| What | File → key | Where to find it |
|---|---|---|
| Public URL | `config.env` → `PUBLIC_URL` | The domain on the ELB certificate |
| SWR registry/org | `config.env` → `SWR`, and `helm/values-sky47.yaml` → every `repository:` | SWR console → Organizations |
| App namespace | `config.env` → `APP_NAMESPACE`, and `routes/*.yaml`, `k8s/frontend-internal.yaml`, `k8s/frontend-lb-rollback.yaml` → `namespace:` | Where the app runs |
| ELB ID | `k8s/gateway-lb.yaml`, `gateway-lb-test.yaml`, `frontend-lb-rollback.yaml` → `kubernetes.io/elb.id` | ELB console → your load balancer → ID |
| Certificate ID | same 3 files → `kubernetes.io/elb.cert-id` | ELB console → Certificates |
| Trusted proxy ranges | `helm/values-sky47.yaml` → `apisix.nginx.http.realIpFrom` | preflight prints the VPC (node) and container (pod) ranges |
| ServiceMonitor | `helm/values-sky47.yaml` → `metrics.serviceMonitor.enabled` | `true` only if preflight finds the ServiceMonitor CRD |
| Rate limit | `routes/*.yaml` → `limit-count` (`count`, `time_window`) | Per IP **per APISIX pod**: effective limit = count × replicas |
| Timezone | `helm/values-sky47.yaml` → `timezone` | |

## New setup (manual)

Everything runs from an admin host in the VPC (for example the Jenkins VM) with:
- the **cluster-admin kubeconfig**, because install creates CRDs and ClusterRoles. Jenkins' namespace-scoped `jenkins-deployer` can't do this, and shouldn't.
- `kubectl`, `helm` v3, `docker`, `curl`, `openssl`, `python3`. Helm doesn't need root: any copy in your `PATH` works.

### 0. Before you start
1. The app is deployed from the app repo (branch `feature/apisix-gateway` or later): `backend`, `orders-backend` and `frontend` Deployments, plus the `backend` and `orders-backend` Services. Use its `K8S-DEPLOYMENT.md` up to step 7.
2. **LTS**: in the CCE ingestion config, set the namespace regex to `^(sample-app|apisix)$`. Otherwise the gateway logs never reach LTS.
3. Point kubectl at the cluster: `export KUBECONFIG=/path/to/cluster-admin-kubeconfig.json`

### 1. Configure and check
Edit the files in [Values per environment](#values-per-environment), then:
```bash
scripts/preflight.sh       # changes nothing; every line should be [ok], or a WARN you understand
```
Read the WARNs, in particular:
- **NetworkPolicy probably NOT enforced**: then the Admin API is reachable from every pod and only its key protects it.
- **No metrics API**: then the HPA can't scale and stays at 2 pods.

### 2. Mirror images to SWR
```bash
scripts/swr-login.sh       # asks for the IAM Access Key Id / Secret Access Key (or set AK / SK env)
scripts/mirror-images.sh   # pulls the pinned images and pushes them to $SWR
docker logout ${SWR%%/*}   # don't leave a long-term login on the host
```
- Docker needs root or the `docker` group. To use another user's Docker, prefix both scripts: `DOCKER="sudo -u jenkins -H docker" scripts/...`
- You can use the console's temporary login command (**SWR → Generate Login Command**) instead of `swr-login.sh`.
- The images are pushed for the host's platform only (amd64), so preflight warns if the nodes are a different architecture.

### 3. Namespace and secrets
```bash
scripts/create-secrets.sh  # namespace apisix, random admin/viewer keys, SWR pull secret (never overwrites)
```

### 4. Install
```bash
YES=1 scripts/install.sh   # without YES=1 it shows a helm diff and asks (if helm-diff is installed)
kubectl -n apisix get pods,hpa,pdb,svc
kubectl -n sample-app get apisixroute,apisixglobalrule
kubectl -n sample-app describe apisixroute sample-app | grep -A3 Conditions     # Accepted=True
kubectl -n apisix logs deploy/apisix-ingress-controller -c manager --since=2m | grep ERROR
```
In the first second you may see `dial unix /sockets/adc.sock: no such file`, while the adc sidecar is still starting. Errors after that need a look.

### 5. Test inside the cluster
`curl` is available in the app's pods (it isn't in the APISIX image). Only read headers, don't write files in the pod:
```bash
for p in /api/health /orders-api/health / /some/spa/route; do
  echo "== $p"; kubectl -n sample-app exec deploy/frontend -- curl -s -o /dev/null -D - "http://apisix-gateway.apisix$p" | grep -iE '^HTTP|^x-request-id'
done
# Is NetworkPolicy enforced? A timeout means yes. Any HTTP code (e.g. 404) means no.
kubectl -n sample-app exec deploy/frontend -- curl -s -o /dev/null -w '%{http_code}\n' --max-time 5 http://apisix-admin.apisix:9180/apisix/admin/routes
```

### 6. Test on the ELB, port 8443 (production 443 untouched)
```bash
kubectl apply -f k8s/gateway-lb-test.yaml        # listener is ready in ~30s
scripts/smoke-test.sh https://<domain>:8443
```
Optional: in the ELB console, restrict listener 8443 to office/VPN IPs (Access Control).
Then check these before cutting over:
```bash
U=https://<domain>:8443
# Rate limit: must show 429s. Needs more than count × replicas requests (600 × 2).
seq 1600 | xargs -P30 -I{} curl -s -o /dev/null -w '%{http_code}\n' $U/api/health | sort | uniq -c
# Real client IP: client_ip must be your public IP, not 10.x or 172.16.x
kubectl -n apisix logs -l app.kubernetes.io/name=apisix --tail=50 | grep '"/api/health"' | tail -2
# Load + pod loss: kill one APISIX pod while this runs. Expect only 200s. "/" has no rate limit.
seq 2000 | xargs -P20 -I{} curl -s -o /dev/null -w '%{http_code}\n' $U/ | sort | uniq -c &
sleep 5; kubectl -n apisix delete $(kubectl -n apisix get pod -l app.kubernetes.io/name=apisix -o name | head -1); wait
# Metrics: both pods "up" in the CCE Prometheus agent
kubectl -n sample-app exec deploy/frontend -- curl -s http://prometheus-lightweight.monitoring:9090/api/v1/targets | grep -o '"scrapePool":"apisix[^"]*"[^}]*"health":"[a-z]*"'
```
For latency, compare the same `curl -w '%{time_total}'` run against `:443` (nginx) and `:8443`. In-cluster, APISIX adds about 0.6 ms.

### 7. Go live on 443
**The cluster already has a `frontend` LoadBalancer Service on 443** (migrating an existing setup): plan an announced window, about 1 minute of outage.
```bash
YES=1 scripts/cutover.sh   # backup → delete test listener + frontend LB svc → apply gateway-lb.yaml → smoke test (retries)
```
The ELB may answer 502 for a few seconds while CCE builds the listener. The script retries for about a minute before it gives up.

**Fresh cluster, nothing on 443 yet:** just switch the test listener to production:
```bash
kubectl -n apisix delete svc apisix-gateway-lb && kubectl apply -f k8s/gateway-lb.yaml && scripts/smoke-test.sh
```

If you need zero downtime, use a second ELB/EIP for `gateway-lb.yaml` and switch DNS (lower the TTL a day ahead).

### 8. Rollback (any time)
```bash
scripts/rollback.sh        # delete gateway LB svc → apply k8s/frontend-lb-rollback.yaml (nginx serves 443 again)
```
Only Services change. Gateway pods/routes and the app Deployments are untouched. Before cutting over again, run `kubectl -n sample-app delete svc frontend`.

**The SPA comes back, but not the APIs.** Since the app's nginx dropped its `/api/` and `/orders-api/` proxy blocks (app repo `039ebc2`), nginx serves only static files.
For a full rollback, also redeploy a frontend image built **before** that change, using Jenkins `ACTION=rollback SERVICES=frontend` or an older `IMAGE_TAG`.

### 9. Afterwards
- Write a record in `docs/deployments/<cluster>.md`: values used, timings, test results, surprises.
- Remove admin credentials you uploaded or downloaded for the install: delete the kubeconfig file and revoke it in CCE, and delete or rotate the AK/SK.
- **Hardening** (after a stable week):
  - Remove the `/api/` and `/orders-api/` blocks from the app's `frontend/nginx.conf` and deploy the frontend (done on beta: app repo `039ebc2`). After that, a rollback also needs an older frontend image (step 8).
  - Create the gateway alarms: [docs/lts-alarms.md](docs/lts-alarms.md) (5xx, slow requests, 429 spike, heartbeat).
  - `kubectl apply -f k8s/networkpolicy-sample-app.yaml` (only useful if NetworkPolicy is enforced), then check the backend pods stay Ready.

## Observing the live gateway

| What | How |
|---|---|
| Live request log (JSON: `client_ip`, `uri`, `status`, `request_time`, `upstream_addr`) | `kubectl -n apisix logs -f -l app.kubernetes.io/name=apisix --max-log-requests 4 \| grep -v /apisix/admin`. Add `\| grep '"status":5'` for errors only |
| One request end to end | Take `X-Request-Id` from the response header and grep the logs for it |
| Logs history / search / alarms | LTS → the CCE log stream, filter `apisix` (needs the namespace regex from step 0). Alarms: [docs/lts-alarms.md](docs/lts-alarms.md) |
| Metrics over time | AOM → Metric Browsing → `apisix_http_status` (by `code`, `matched_uri`), `apisix_http_latency_bucket`, `apisix_http_requests_total`, `apisix_bandwidth`, `apisix_nginx_http_current_connections`. E.g. 5xx rate per path: `sum(rate(apisix_http_status{code=~"5.."}[5m])) by (matched_uri)` (`route` is an ID hash) |
| Raw metrics now | `kubectl -n sample-app exec deploy/frontend -- curl -s http://apisix-prometheus-metrics.apisix:9091/apisix/prometheus/metrics \| grep apisix_http_status` |
| Config APISIX is running | `kubectl -n apisix port-forward deploy/apisix 9090:9090 &` then `curl -s localhost:9090/v1/routes`, `curl -s localhost:9090/v1/healthcheck` |
| Route sync status | `kubectl -n sample-app describe apisixroute sample-app`, controller logs `-c manager` |
| Is a response from APISIX? | Look for an `X-Request-Id` header. The ELB rewrites `Server` to `elb`, so that header proves nothing |

There's no dashboard UI: the chart only enables APISIX's built-in UI when etcd is used. AOM (charts) plus LTS (logs) cover it.
The controller's config push (`PUT /apisix/admin/configs`, every 10s) shows up in the access log, so filter it out.

## Day-2 operations

**Add a route.** Add an `http[]` entry to `routes/*.yaml`, open a PR, and once it's merged run `kubectl apply -f routes/`.
Give specific paths a `priority` above 0; the SPA catch-all is `priority: 0`.

**Upgrade.** Bump `CHART_VERSION` and the image tags in `config.env` and `values-sky47.yaml`. Read the chart and APISIX changelogs, then run `mirror-images.sh` and `install.sh`.
Helm **does not upgrade CRDs**. If the controller version changes, apply the new CRDs first:
`helm show crds apisix/apisix --version <v> | kubectl apply --server-side -f -`.

**Rotate the Admin key.** Run `kubectl -n apisix delete secret apisix-admin && scripts/create-secrets.sh`, then `kubectl -n apisix rollout restart deploy/apisix deploy/apisix-ingress-controller`.

**Backup / DR.** There's no state to back up. Git (this repo plus the app repo manifests) is the whole configuration, so recovering means steps 2–7 again.

## Troubleshooting

| Symptom | Check |
|---|---|
| APISIX pods stuck NotReady | Controller logs (`-c manager`, `-c adc-server`): wrong admin key, Admin API port blocked, or the `apisix-config` GatewayProxy is missing |
| 404 from APISIX for a path | `kubectl -n sample-app describe apisixroute sample-app` status. Check `ingressClassName: apisix` and the path/priority |
| 502/504 from APISIX | Upstream Service/endpoints exist (`kubectl -n sample-app get ep`), and the pod is Ready. APISIX's access log shows `upstream_status` |
| 502 for a few seconds right after creating/changing a listener | The ELB is still building it. Retry. If the APISIX access log has no 502, it wasn't APISIX |
| `ImagePullBackOff` | Images mirrored? `default-secret` present in `apisix`? |
| `docker push` says `You may not login yet` | No (or expired) SWR login for the Docker user doing the push. Run `swr-login.sh` with the same `DOCKER=` |
| Client IP is a 10.x/172.16.x address | `realIpFrom` doesn't cover the ELB/node or container range (see preflight) |
| Rate limit never triggers in tests | It's per pod: send more than `count × replicas` within `time_window` |
| HPA `cpu: <unknown>` | No metrics API in the cluster (preflight warns). It stays at `minReplicas` |
| Listener 443 not created / `apply` forbidden (`protocol_port 443 … already … used`) | Another Service still holds 443 on that ELB (`frontend`?). Delete it first. CCE events: `kubectl -n apisix describe svc apisix-gateway-lb` |
| `kubectl apply` of the app's old `frontend.yaml` fails: `protocol_port 443 … already has been usesd by other service` | Expected: CCE refuses a second Service on 443 of the same ELB, so the gateway keeps traffic. Use the app repo's `feature/apisix-gateway` manifests (no Service) |
| No gateway logs in LTS | Ingestion namespace regex (step 0) |
