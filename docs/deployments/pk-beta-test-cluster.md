# Deployment log: pk-beta-test-cluster

APISIX API gateway in front of the sample app, serving **https://beta-test.paykoko.pk**. Live since **2026-10-26 09:30 UTC**.

| | |
|---|---|
| Cluster | `pk-beta-test-cluster`, CCE k8s v1.31.6 (HCS), 1 node `10.10.1.156` (amd64, 16 vCPU, 30 GB) |
| ELB | shared ELB `a168acbd-ad28-4c82-a57d-7229be2b7da8`, EIP 138.252.175.35, private 10.10.1.229, cert `fb0464e7b74948d6a3223f05eafbd311` (`*.paykoko.pk`) |
| Networks | VPC 10.10.0.0/16 (nodes), containers 172.16.0.0/16 |
| Admin host | `ecs-c9d3-beta-1` (Jenkins VM, 10.10.1.250, egress IP 138.252.175.13) |
| Versions | chart `apisix` 2.17.1, APISIX 3.18.0-ubuntu, ingress controller 2.2.0, adc 0.27.1 (mirrored to `swr.pk-isb-1.sky47.com/pk-beta-repo/`) |
| Repo state | this repo `feat/apisix-gateway-cce` @ `2950ddc` (deploy), app repo `feature/apisix-gateway` @ `ece6d71` |

## Starting state
- The app ran in `sample-app` with images `main-aa54b9a-3` (deployed by Jenkins). The repo manifests had older tags, so rollback must never re-apply the Deployments.
- Public 443 went to the `frontend` LoadBalancer Service (nginx), which proxied `/api/` and `/orders-api/`.
- Baseline through nginx: `/`, `/api/health`, `/orders-api/health` all 200.
- CCE monitoring add-on present: prometheus-operator plus `prometheus-lightweight` (agent, remote-write to AOM), selecting every ServiceMonitor.
- No metrics API (`kubectl top` fails), and no canal/yangtse DaemonSet.

## Steps (UTC)
1. Repo config: `PUBLIC_URL`, `realIpFrom` = 127.0.0.1, 10.10.0.0/16, 172.16.0.0/16, ServiceMonitor on, NetworkPolicy allows monitoring → 9091 (commit `36e68d5`).
2. **09:13–09:22, images.** The jenkins user had no SWR login (the first push failed with `You may not login yet`).
   Logged in with a long-term login derived from an IAM AK/SK (now `scripts/swr-login.sh`), ran `mirror-images.sh` as jenkins, then `docker logout`.
3. Namespace `apisix`, Secret `apisix-admin` (random keys, created in-cluster only). CCE had already auto-created `default-secret` in the new namespace.
4. **09:23 install.** `install.sh` succeeded in about 40s. The controller logged adc.sock errors in the first second (sidecar starting), then none.
5. In-cluster tests: all 4 paths 200 with `X-Request-Id`. **Admin API 9180 reachable from `sample-app`, so NetworkPolicy is not enforced.**
6. **09:25 test listener 8443.** Ready in about 25s on EIP .35.

   | Test | Result |
   |---|---|
   | smoke | 4/4 |
   | real client IP | `client_ip` = 138.252.175.13 (the test host) |
   | rate limit | 700 requests: no 429, because the limit is per pod (600 × 2). 1600 requests: 498 × 200 / 1102 × 429. Orders route unaffected (own counter) |
   | load, `/`, 2000 requests at 20 parallel, one APISIX pod deleted | **2000/2000 200**, replacement Ready in ~15s |
   | latency, public, back-to-back | 443 nginx p50 64–65 / p95 83–131 ms. 8443 APISIX p50 64–65 / p95 83–86 ms |
   | latency, in-cluster | APISIX 1.4 ms vs nginx 0.8 ms (p50) |
   | metrics | both pods `up` in prometheus-lightweight targets |

7. **09:30:09 cut-over** (`cutover.sh`). Deleted `apisix-gateway-lb` (8443) and `frontend`, then applied `gateway-lb.yaml`. `/api/health` was 200 at 09:30:59, so the outage was **about 50s**.
   One smoke check (`/some/spa/route`) got a **502 from the ELB** while the listener was still being set up. The APISIX log had no 502, and retests were all 200.
   `cutover.sh` now retries the smoke test.
8. 09:32: final smoke test 4/4 on 443. `X-Request-Id` present, and APISIX logs show the correct upstream pods.
9. Backup of the old Service: `backup/frontend-svc-20261026-093009.yaml` (local, gitignored). The same Service is in git as `k8s/frontend-lb-rollback.yaml`.

## Findings
- **NetworkPolicy is not enforced on this cluster.** The Admin API is protected only by its key. The policies are applied anyway.
- **The HPA can't scale** (no metrics API): it reads `cpu: <unknown>` and stays at 2 pods. That's fine for beta. Enable a CCE metrics add-on if autoscaling is needed.
- The ELB rewrites `Server` to `elb`. Use `X-Request-Id` to tell whether a response came through APISIX.
- The Service status lists 138.252.175.13 as well as .35. .13 is the Jenkins VM's egress IP, which serves Jenkins. Not an issue.
- CCE rejects a second Service on the same ELB port: a server dry-run of the old `frontend` LB Service failed with `protocol_port 443 … already has been usesd by other service`. So the old app manifest can't take 443 back by accident. It fails instead.
- The APISIX image has no `curl`. Run in-cluster tests from the app pods instead (read-only, no files written).

## Open items
- [ ] LTS ingestion namespace regex → `^(sample-app|apisix)$` (console)
- [ ] Revoke the cluster-admin kubeconfig used for the install (valid until 2031-10-26), and delete/rotate the IAM AK/SK used for the SWR login
- [ ] Merge this repo's `feat/apisix-gateway-cce` and the app repo's `feature/apisix-gateway`. Then `git pull` in the root-owned checkout `/home/ubuntu/beta-pk/sample-project-sky-47-deploy-test`. Until then, applying that checkout's `k8s/frontend.yaml` fails on its Service (443 taken), so use the new branch
- [ ] Hardening (README step 9), started 2026-10-26 at the operator's request:
  - [x] nginx API proxy blocks removed in the app repo (branch `feature/remove-nginx-api-proxy` @ `039ebc2`)
  - [ ] merge it, then run Jenkins `deploy`, `REF=origin/main`, `SERVICES=frontend`, and check `scripts/smoke-test.sh` plus the UI
  - [ ] LTS log structuring + 4 alarms + SMN topic ([docs/lts-alarms.md](../lts-alarms.md))
  - [ ] skipped: `networkpolicy-sample-app.yaml`, because NetworkPolicy isn't enforced on this cluster

## Observability stack (2026-10-26, 18:12–18:45 UTC)
One Grafana for logs, traces and metrics: `https://grafana.paykoko.pk`, allowlist `14.1.78.0/24` plus login. Branch `feat/observability`.
How it was done: in one continuous run, with an automatic check after each stage.

| Stage | Result |
|---|---|
| Images | 7 images to SWR. Docker's containerd store couldn't push `grafana:*-distroless` or `tempo` (tiny shared layers). Grafana switched to `13.2.3`; tempo, alloy, prometheus, reloader and the Java agent went through `scripts/registry-copy.py` (byte-identical, digests in `observability/config.env`) |
| Stack | 5 releases. Fixes needed: CCE already owns ClusterRole `prometheus-server` (renamed to `obs-prometheus`); quota `limits.cpu` 4 → 5; Alloy needed `/var/lib/containerd/container_logs` (on CCE `/var/log/pods` is a symlink to it); scrape jobs restricted to the metrics port. Check: all targets up, Loki has every pod's logs |
| Gateway traces | `opentelemetry` enabled (it ships disabled). **APISIX 3.18 reads the collector settings from plugin metadata**, set through the GatewayProxy `pluginMetadata`. Check: `trace_id` in the access log, spans in Tempo. **Tempo down: 1000/1000 OK, p95 79 → 82 ms** |
| orders-backend | Java agent 2.32.0 by patch. Rollout 52s, 0 restarts, image unchanged, memory 27% of the limit. Trace: apisix → orders-backend HTTP → repository → JDBC `SELECT ordersdb.orders` |
| backend | Same: rollout 52s, 0 restarts, memory 27%, traces rooted at apisix |
| Grafana route | From outside the allowlist, `403 Access denied` on every path (including `/api/*`, so the host route wins over the app routes). Unauthenticated API returns 401. Both dashboards provisioned, 0 errors after disabling plugin preinstall |

Findings:
- **Someone `kubectl apply`'d the app manifests at 15:53–16:04.** Backends went back to `1.1.0`/`1.0.0`, and DB settings moved to ConfigMap `db-config` (new RDS `10.10.1.184`; 3306 was unreachable until fixed on the RDS side).
  Jenkins #7 (`deploy-existing main-11bc939-6`) restored the images. The app repo's K8S-DEPLOYMENT.md now warns about this.
- Kubelet probes create no traces (`parentbased_always_off`): no backend-rooted traces in 15 min.
- The Tempo service graph shows user → apisix and backends → MySQL, **but not apisix → backends**, because APISIX emits only a SERVER span (no CLIENT span for the upstream).
  The traces themselves are linked correctly.
- Grafana with `Recreate` is briefly unavailable on every restart: the operator saw two 502/503s at 18:42:37 during an upgrade.
- APISIX warns "collector.address with no TLS". This is accepted: in-cluster plain HTTP to Tempo.

Open items:
- [ ] Merge `feat/observability` (gateway) and `feature/otel-tracing` (app)
- [ ] Create per-person Grafana accounts. The admin password is in Secret `observability/grafana-admin`
- [ ] AOM self-monitoring alarms (`docs/lts-alarms.md`, last section)
- [ ] Revoke the cluster-admin kubeconfig and the AK/SK used today, and delete the uploaded files

### 2026-10-26 18:5x: Grafana OOMKilled
3 restarts with exit 137 at the 512Mi limit while the operator was using it. Grafana 13 runs each datasource plugin as its own process, ~470Mi in normal use.
Raised to request 512Mi / limit 1536Mi, plus `GOMEMLIMIT=1200MiB`. Disk was not the cause (1.7MB of 1.9GB used), so it stays 2Gi.

### 2026-10-26 19:10–19:30: 6 more dashboards (8 in 3 folders)
- Prometheus: OTLP receiver on, `observability` and `kubelet` jobs added (11 targets up). Head series went from 13.8k to 25.6k, mostly Grafana internals. The unused ones are now dropped. Prometheus memory 114Mi.
- Backends: Java agent metrics export on, through instrument.sh (orders, then backend; ~51s each, 0 restarts, images unchanged). 152 series per service.
- `verify-dashboards.py`: 107 panel queries. The first run found 4 problems, all fixed:
  - APISIX non-JSON log lines broke `| json` aggregations
  - the latency bucket label is `le="500.0"`
  - a "down" series was empty when nothing was down
  - one panel is legitimately empty
  Final run: **all pass**.
- In the last hour the 5xx were only on `grafana.paykoko.pk`, during the Grafana OOM window. App routes had none.
- Script fix: instrument.sh now checks the newest pod (it used to read the logs of a pod that was still terminating).

