# Observability: logs, traces and metrics in one Grafana

One place to look at the gateway and the app: **https://grafana.paykoko.pk** (IP allowlist + login).
LTS and AOM keep working unchanged, and **alerting stays there** (`../docs/lts-alarms.md`). Grafana is for looking, not paging.
The reason is that the stack runs on the same single node as the app.

```
Browser → ELB 443 → APISIX ─ host grafana.paykoko.pk (ip-restriction, login) → Grafana
                           └ /api, /orders-api, /  → app            (APISIX injects W3C traceparent)
Traces : APISIX opentelemetry plugin ─┬→ Tempo :4318 (OTLP/HTTP) ─ metrics-generator → Prometheus
         OTel Java agent (backends) ──┘                              (RED metrics, service graph)
Logs   : Alloy DaemonSet (/var/log/pods, ns sample-app|apisix|observability) → Loki
Metrics: Prometheus scrapes APISIX :9091, cAdvisor, CCE node-exporter + kube-state-metrics (ns monitoring)
Grafana: Prometheus + Loki + Tempo, linked by trace_id in both directions
```

| Component | Chart (pinned in `config.env`) | Storage | Retention |
|---|---|---|---|
| Prometheus v3.15.0 | prometheus-community/prometheus 29.36.1 (server only) | 10Gi EVS | 7d / 8GB |
| Loki 3.7.8 | grafana-community/loki 18.15.1 (single binary) | 10Gi EVS | 168h (compactor) |
| Tempo 3.1.0 | grafana-community/tempo 3.1.0 (single binary) | 10Gi EVS | 168h |
| Alloy v1.20.1 | grafana/alloy 1.13.1 (DaemonSet) | hostPath positions | – |
| Grafana 13.2.3 | grafana-community/grafana 13.5.0 | 2Gi EVS | – |

Safety rails:
- PriorityClass `observability-low` (-10): these pods yield to the app under pressure.
- ResourceQuota `observability`: requests 2 CPU / 4Gi, limits 4 CPU / 7Gi, 50Gi storage.
- Limits on every container.
- Everything is ClusterIP. Only Grafana is published, through APISIX.

## Install (new cluster, manual)
Prerequisites: the gateway is installed (`../README.md`), and you have the cluster-admin kubeconfig and `helm` v3.

1. **Images**: they're in the gateway's `../config.env` `IMAGES` list, so `scripts/swr-login.sh && scripts/mirror-images.sh` (from the repo root) mirrors them too.
   Record the digests in `observability/config.env`.
2. **Secrets**: `observability/scripts/secrets.sh` creates a random Grafana admin password and copies the pull secret if CCE didn't create one.
3. **Stack**: `observability/scripts/install.sh`. It runs 5 `helm upgrade --install --atomic` releases and creates the dashboards ConfigMap.
   Check: `kubectl -n observability get pods,pvc`. Every pod should be Ready and every PVC Bound.
4. **Gateway traces**: the gateway values already carry the `opentelemetry` plugin attrs, and `routes/` has the global rule.
   Order matters: run `scripts/install.sh` from the repo root (APISIX restarts with the plugin enabled), and only *then* `kubectl apply -f routes/`.
   If the routes go first, the controller's push is rejected (unknown plugin).
5. **Backend traces**: `observability/scripts/instrument.sh orders-backend`, then `observability/scripts/instrument.sh backend`.
   Each one patches its Deployment and keeps the running image. It undoes itself if the rollout or the checks fail.
6. **Expose Grafana**:
   - Put the office/VPN public CIDRs in `routes/grafana.yaml` (`ip-restriction.whitelist`), then `kubectl apply -f observability/routes/grafana.yaml`.
   - Add a DNS A record `grafana.<domain>` → the ELB EIP.
   - Check: from an allowed IP you get the login page; from any other IP, `403 Access denied`.
7. **Users**: log in as `admin` with the password from
   `kubectl -n observability get secret grafana-admin -o jsonpath='{.data.admin-password}' | base64 -d; echo`.
   Create one account per person (Viewer by default; Editor only for people who build dashboards). Use `admin` only for administration.

Until step 6, open Grafana with `kubectl -n observability port-forward svc/grafana 3000:80`, then http://localhost:3000.

## Using it
- **Dashboards → Gateway → "Gateway overview"**: one screen with:
  - req/s, 5xx share, 429/min and p95
  - traffic by path and status
  - p95 and errors per service (from traces)
  - gateway 5xx log lines and app ERROR/WARN lines
  - a table of slow or failed traces
  - memory and restarts per pod
- **"Apache APISIX (official)"**: the upstream APISIX dashboard (all `apisix_*` metrics).
- **Log → trace**: in any Loki log line with a `trace_id` (APISIX JSON log, or `[trace_id=…]` in Spring Boot lines), click **TraceID**.
- **Trace → logs**: in a trace, click **Logs for this span**. It runs `{namespace=~"sample-app|apisix"} |= "<trace id>"`.
- **Service graph**: Explore → Tempo → Service Graph shows user → apisix and backend / orders-backend → MySQL.
  There's no apisix → backend edge, because APISIX emits only a SERVER span. The traces themselves still link gateway and backend spans.
- Explore queries worth keeping:
  ```
  {namespace="apisix", container="apisix"} | json | status >= 500
  {namespace="apisix", container="apisix"} | json | request_time > 1
  {namespace="sample-app"} |= "Exception"
  { resource.service.name = "orders-backend" && duration > 300ms }        (TraceQL)
  ```

## Configuration notes
- **Metric labels use names**: the global rule's `prometheus` plugin has `prefer_name: true`, so the `route` label is the rule name, for example
  `sample-app_sample-app_catalog-api` (`<namespace>_<ApisixRoute>_<rule name>`), instead of the controller's hash ID.
  The `service` label is `<namespace>_<ApisixRoute>_<rule index>` (0 = first rule), because ApisixRoute v2 can't name it, so filter on `route`.
  Series from before the change (hash IDs) age out with the 7-day retention.
- **Sampling**: APISIX samples every request on beta (`routes/sample-app.yaml`, `sampler: always_on`).
  For production use `{name: trace_id_ratio, options: {fraction: 0.1}}`. The backends follow the gateway's decision (`parentbased_always_off`).
- **Exporters never block traffic**: APISIX uses `drop_on_queue_full: true`, and the Java agent exports asynchronously with a bounded queue.
  If Tempo is down, spans are dropped and requests are unaffected.
- **Private data**: the access log's `path` field has no query string. The Java agent's SQL sanitizer (on by default) replaces literals in `db.statement`.
  Data is kept 7 days and Grafana is behind an allowlist and login.
- **Log volume**: Alloy drops the controller's `/apisix/admin/configs` pushes (every 10s per pod). LTS keeps them for the heartbeat alarm.
  Loki labels are only `namespace`, `app`, `container`, `pod`.
- **Production storage**: switch Loki and Tempo to OBS (S3-compatible).
  - Loki: `loki.storage.type: s3`, plus `s3.endpoint: obs.<region>.<domain>`, `bucketnames`, and access keys from a Secret.
  - Tempo: `tempo.storage.trace.backend: s3`, with the same OBS settings.
  - Keep the PVCs for the WAL only.
- **Accepted beta risks**: Loki, Tempo and Prometheus have no auth (ClusterIP only), and NetworkPolicy isn't enforced on this cluster.
  The whole stack is on the single node, so it goes down with it (alerting is in AOM/LTS for that reason).

## Upgrade
Bump the chart version in `config.env` (charts ≥ 2 weeks old), and the image tag in `values/*.yaml` and `../config.env` `IMAGES`.
Then mirror the images, record the digests, and re-run `install.sh`. Read each chart's upgrade notes first (Tempo 3.x and Loki change config keys between majors).

## Remove
Each piece can be removed on its own. User traffic isn't affected, apart from the backend restarts.
```bash
kubectl -n sample-app rollout undo deploy/orders-backend deploy/backend   # drop the Java agent
# gateway: remove opentelemetry from helm/values-sky47.yaml (plugins, pluginAttrs, log format) and routes/sample-app.yaml, then re-run install.sh + apply routes/
kubectl delete -f observability/routes/grafana.yaml
for r in grafana alloy tempo loki prometheus; do helm -n observability uninstall $r; done
kubectl delete ns observability && kubectl delete priorityclass observability-low     # deletes the PVCs, so the data is lost
```

## Troubleshooting
| Symptom | Check |
|---|---|
| Pod `Pending`, event `exceeded quota` | `kubectl -n observability describe quota`. Raise `k8s/resourcequota.yaml` or lower the requests |
| PVC `Pending` | StorageClass `csi-disk-topology` binds when the pod is scheduled. `kubectl describe pvc` shows EVS errors |
| No logs in Loki | `kubectl -n observability logs ds/alloy`. Check the paths under `/var/log/pods` and the `HOSTNAME` node filter |
| No traces | APISIX: `kubectl -n apisix logs deploy/apisix \| grep -i opentelemetry`. Tempo: `kubectl -n observability logs sts/tempo`. Backends: look for the agent's startup line `opentelemetry-javaagent` |
| APISIX logs `plugin_metadata is required for opentelemetry` | Collector settings must be plugin **metadata** in APISIX 3.18 (GatewayProxy `pluginMetadata` in `helm/values-sky47.yaml`), not `plugin_attr` |
| `ClusterRole "prometheus-server" exists and cannot be imported` | CCE's monitoring add-on owns that name. Our release uses `server.fullnameOverride: obs-prometheus` |
| Alloy ships nothing | On CCE `/var/log/pods` is a symlink to `/var/lib/containerd/container_logs`. Both must be mounted (values/alloy.yaml) |
| Image push: `does not provide the specified platform` / `content digest … not found` | Docker containerd-store bug with tiny shared layers. `mirror-images.sh` falls back to `scripts/registry-copy.py` |
| Controller sync errors after adding the plugin | The routes were applied before APISIX restarted with `opentelemetry` in its plugin list (install step 4 order) |
| Logs show `trace_id=` empty | That request wasn't sampled (not through APISIX, e.g. a probe). This is expected |
| `grafana.<domain>` shows the app | The DNS record is missing, or the host doesn't match `routes/grafana.yaml` |
| 403 on Grafana | Your public IP isn't in the allowlist (`curl ifconfig.me`) |
