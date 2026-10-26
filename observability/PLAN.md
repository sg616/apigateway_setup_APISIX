# One dashboard for logs, traces and metrics: Grafana stack on CCE (reviewed)

## Context
The user wants APISIX and app **logs, traces and metrics in one place**.
- Today: logs are in LTS (ICAgent), metrics in AOM (CCE Prometheus agent, no query API), and there are **no traces**.
- APISIX's built-in `/ui/` is a config editor and doesn't work in etcd-less mode.

Decisions (user, 2026-10-26):
- the in-cluster Grafana stack
- public `https://grafana.paykoko.pk` with an **IP allowlist** and login
- tracing in APISIX **and** both backends
- **7-day** retention
- the work on **separate branches**: `feat/observability` in the gateway repo, `feature/otel-tracing` in the app repo. Nothing goes to `main`
- the plan reviewed the way a senior DevOps engineer would (findings below)

LTS/AOM stay as they are, and **alerting stays there**, outside the cluster.

## Senior DevOps review: what changed from the first draft
1. Grafana: allowlist (`ip-restriction`), then login. Per-person accounts with the Viewer role by default; the admin account is for setup only. No anonymous access or sign-up.
2. Single node: monitoring dies along with the app, so **alerts stay in LTS/AOM** (`docs/lts-alarms.md`). Grafana is for looking, not paging.
3. Resource safety: `PriorityClass observability-low` (value -10, below app pods), limits on every pod, and a `ResourceQuota` on `observability` (cpu 3, memory 6Gi, storage 50Gi).
4. **Three gated phases** instead of one big-bang rollout (below).
5. Tracing must never hurt traffic. Both exporters are async with bounded queues. Test: Tempo scaled to 0 during load must leave p95 unchanged with 0 errors.
6. Java agent overhead is measured on orders-backend first. Gate: Ready under 60s, no liveness restarts, memory under 70% of the 1Gi limit.
7. Private data: the agent's SQL sanitizer stays on (default). The access log gets a `path` field without the query string, used in Loki. 7-day retention, access restricted.
8. Log noise: Alloy drops `/apisix/admin/configs` lines (LTS keeps them for the heartbeat alarm). Loki labels limited to namespace/app/container/stream. Alloy keeps its file positions on a node hostPath.
9. Sampling ratio in `observability/config.env` (`TRACE_SAMPLE_RATIO`, beta 1.0). Backends use `parentbased_always_off`, so they only record traces that APISIX started (no probe noise).
10. Storage: EVS `csi-disk-topology`, filesystem Loki/Tempo. Fine for beta. The README documents the OBS (S3-compatible) backend for production.
11. Watch the watchers: AOM alarms on `kube_pod_container_status_restarts_total{namespace="observability"}` and on pods not Ready (kube-state-metrics already in AOM). Added to `docs/lts-alarms.md`.
12. Supply chain: every image mirrored to SWR, tag **and digest** recorded in `observability/config.env`.
13. Loki, Tempo and Prometheus stay ClusterIP only (no auth, and NetworkPolicy isn't enforced here). Documented as an accepted beta risk.
14. Dashboards provisioned from git with `allowUiUpdates: false`.

## Versions (charts ≥ 2 weeks old)
| Component | Chart | App |
|---|---|---|
| Grafana | grafana-community/grafana 13.5.0 | 13.2.3 |
| Loki | grafana-community/loki 18.15.1 | 3.7.8 |
| Tempo | grafana-community/tempo 3.1.0 | 3.1.0 |
| Alloy | grafana/alloy 1.13.1 | 1.20.1 |
| Prometheus | prometheus-community/prometheus 29.36.1 | 3.15.0 |
| OTel Java agent | `ghcr.io/open-telemetry/opentelemetry-operator/autoinstrumentation-java` 2.x | pick a tag ≥ 2 weeks old at step 1 |

## Architecture
```
Browser → ELB 443 → APISIX ─ host grafana.paykoko.pk (+ip-restriction) → Grafana (ns observability)
                           └ /api,/orders-api,/  → app (W3C traceparent injected)
Traces : APISIX opentelemetry ─┬→ Tempo :4318 ─ metrics-generator ─→ Prometheus (RED, service graph)
         OTel Java agent ──────┘
Logs   : Alloy DaemonSet (ns sample-app|apisix|observability) → Loki
Metrics: Prometheus scrapes APISIX :9091 + CCE node-exporter / kube-state-metrics (ns monitoring)
Grafana: Prometheus + Loki + Tempo, linked by trace_id both ways
```

## Files
**Gateway repo, branch `feat/observability`:**
- `observability/PLAN.md` (this plan, committed first for review)
- `observability/README.md` (runbook, access, correlation how-to, the OBS option, troubleshooting)
- `observability/config.env` (chart repos/versions, image digests, namespace, host, allowlist CIDRs, retention, sample ratio, StorageClass)
- `observability/k8s/` (`namespace.yaml`, `priorityclass.yaml`, `resourcequota.yaml`)
- `observability/values/{prometheus,loki,tempo,alloy,grafana}.yaml`
- `observability/dashboards/`: `apisix-official.json` (grafana.com 11719) and `gateway-overview.json` (one screen: RED metrics + error logs + recent traces)
- `observability/routes/grafana.yaml` (ApisixRoute, host match, `ip-restriction`, `limit-count`)
- `observability/patches/otel-java.yaml` (strategic-merge patch for the backend Deployments)
- `observability/scripts/{install.sh,secrets.sh,verify.sh}`
- `helm/values-sky47.yaml`: `pluginAttrs.opentelemetry` (collector, `set_ngx_var`), plus `trace_id` and `path` in the access log
- `routes/sample-app.yaml`: `opentelemetry` in the ApisixGlobalRule
- `config.env` `IMAGES`: plus the new images

**App repo, branch `feature/otel-tracing`:**
- `k8s/backend.yaml`, `k8s/orders-backend.yaml`: an initContainer copies `javaagent.jar` into an emptyDir, plus the `JAVA_TOOL_OPTIONS`, `OTEL_*` and `LOGGING_PATTERN_LEVEL=%5p [trace_id=%X{trace_id}]` env vars
- `K8S-DEPLOYMENT.md`: tracing section
- Live rollout is a **`kubectl patch`**, never `apply`, because the manifests carry older image tags than the live `main-aa54b9a-3`.

## Steps
0. Commit this plan as `observability/PLAN.md` on a new branch `feat/observability`, push it, and give the PR link.
1. Write all files. Run `helm template` for all 5 charts and check: renders clean, PVCs, no NodePort/LB, limits and priorityClass on every pod, the image list.
   Run `kubectl apply --dry-run=server` on the k8s objects, route and patch. Commit and push to the branches.
2. **User**: SWR login (a *new* AK/SK, or the console's temporary command), then the short mirror command I give. Afterwards I log out and record the digests.
3. **User**: provide the allowlist CIDRs and create a DNS A record `grafana.paykoko.pk` → 138.252.175.35.

**One continuous run** (user, 2026-10-26: all phases at once, no stops for approval between them).
Each stage still ends with an **automatic check** that takes seconds. If a check fails, I roll back only that stage, stop, and report. Otherwise I keep going.
Order inside the run: platform → gateway → orders-backend → backend → expose.

4. **Stack**: namespace, PriorityClass, quota, `secrets.sh`, `install.sh` (5 Helm releases).
   Check: pods Ready, PVCs Bound, Prometheus target APISIX `up`, Loki has lines from all 3 namespaces, smoke test 4/4.
5. **Gateway traces**: gateway `install.sh` (rolling APISIX restart, PDB keeps 1 pod) + `kubectl apply -f routes/`.
   Check: smoke test 4/4, `trace_id` in the APISIX log, APISIX spans in Tempo. Tempo-down test during a short load: p95 unchanged, 0 errors.
6. **Backends**: `kubectl patch` orders-backend, then backend (the backends restart one at a time, maxSurge 1, so no downtime).
   Check per service: Ready under 60s, no restarts, memory under 70% of the limit, spans join the APISIX trace, logs carry `trace_id=`. If orders fails, backend is not patched.
7. **Expose**: apply the Grafana route with the allowlist.
   Check: login page from an allowed IP, 403 from any other IP (tested from this VM, which isn't on the list).
   If you haven't sent the CIDRs/DNS by then, the route is applied with an empty allowlist (everyone gets 403) and Grafana is reachable via port-forward until you do.
8. Update the deployment record and the AOM self-monitoring alarm docs. Commit and push (sg616, no Claude attribution). Final report to the user.
   The user reads the admin password with `kubectl get secret` and creates per-person accounts.

Your input is needed **only** at step 2 (the SWR mirror command) and step 3 (CIDRs + DNS). Steps 3 and 4 run in parallel, so DNS can propagate while the stack installs.

## Verification
- **Gateway overview** dashboard: req/s, 5xx, 429, p95, an error-log panel and recent traces, all filled after `smoke-test.sh` and a short load.
- One trace: APISIX → orders/backend HTTP → JDBC spans. "Logs for this span" opens the matching Loki lines.
- The service graph shows APISIX → backend/orders-backend → MySQL.
- `https://beta-test.paykoko.pk` smoke test 4/4 after every phase, and public p95 unchanged within noise.
- Retention flags (Prometheus 7d, Loki and Tempo 168h) are in the rendered manifests. PriorityClass and limits are on every observability pod.

## Rollback
Each phase can be undone on its own, and none of these steps touch user traffic apart from the backend restarts:
- **C**: `kubectl rollout undo deploy/<svc>`
- **B**: revert the 2 gateway files and re-run `install.sh`
- **A**: `helm uninstall` the 5 releases and delete the namespace (PVCs included)
- **Expose**: delete the Grafana route

