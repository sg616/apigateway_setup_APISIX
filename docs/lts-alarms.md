# Gateway alarms in LTS

Four alarms on the APISIX access log, created in the sky47 console. The console can't be scripted from this repo.
Menu names follow LTS 2.x on HCS. Check the [Khazana help center](https://docs.financialkhazanapk.cloud/mohelpcenter/operation/en-us/index.html) (LTS → Alarms) if yours differ.

| # | Alarm | Fires when (5-minute window, checked every 5 min) | Severity |
|---|---|---|---|
| 1 | Gateway 5xx | ≥ 20 responses with `status >= 500` | Critical |
| 2 | Gateway slow | ≥ 50 requests with `request_time > 1` second | Major |
| 3 | Rate-limit spike | ≥ 200 responses with `status = 429` | Minor |
| 4 | Gateway heartbeat lost | **no** controller config pushes (`/apisix/admin/configs`) | Critical |

The thresholds suit beta traffic (thousands of requests/hour). Tune them after a week of real numbers (query 1–3 without the `HAVING` part shows normal levels).
Alarm 4 works because the ingress controller pushes config to every APISIX pod every 10s and each push is logged. If those lines stop, either APISIX or the controller is down, or logs aren't arriving.

## 0. Prerequisites
1. **Log collection includes the gateway**: LTS → Log Ingestion → your CCE ingestion config → namespace regex `^(sample-app|apisix)$`.
   Check: LTS → Log Management → your stream → search `apisix_` or `"/apisix/admin/configs"` and expect lines from the last minute.
2. **Notifications**: create an SMN topic with e-mail (or SMS) subscribers, for example `gateway-alarms`, and confirm each subscription from its e-mail.
   In LTS, link it under Alarms → Alarm Action Rules (or pick it directly in each rule).

## 1. Structure the log (so SQL can read the fields)
APISIX writes one JSON object per request to stdout (format in `helm/values-sky47.yaml`). Example:
```json
{"time":"2026-10-26T09:31:00+00:00","client_ip":"203.0.113.7","request_id":"…","method":"GET","host":"beta-test.paykoko.pk","uri":"/api/health","status":200,"bytes":57,"request_time":0.004,"upstream_addr":"172.16.0.78:8080","upstream_status":"200","upstream_time":"0.004","user_agent":"curl/8.5.0"}
```
LTS → Log Management → your stream → **Log Structuring** (Cloud structuring) → **JSON**. Pick one APISIX line above as the sample and keep these fields with these types:

| Field | Type |
|---|---|
| `status` | long |
| `request_time` | float |
| `uri`, `client_ip`, `host`, `method`, `upstream_addr`, `upstream_status`, `request_id` | string |

The same stream also carries the Spring Boot and nginx lines. They don't parse as JSON, so they just have empty fields and drop out of the queries below.

## 2. Create the SQL alarm rules
LTS → **Alarms → Alarm Rules → Create → SQL alarm**. For each rule: pick your log group/stream, query period **last 5 minutes**, frequency **every 5 minutes**, then the condition, severity and action rule.

**1. Gateway 5xx** (Critical), condition `errors >= 20`
```sql
SELECT COUNT(*) AS errors WHERE status >= 500
```

**2. Gateway slow** (Major), condition `slow >= 50`
```sql
SELECT COUNT(*) AS slow WHERE request_time > 1 AND uri NOT LIKE '/apisix/%'
```

**3. Rate-limit spike** (Minor), condition `limited >= 200`
```sql
SELECT COUNT(*) AS limited WHERE status = 429
```

**4. Gateway heartbeat lost** (Critical), condition `pushes < 1`
```sql
SELECT COUNT(*) AS pushes WHERE uri = '/apisix/admin/configs'
```
If your LTS version has no SQL alarms, create 4 as a **keyword alarm** on `"/apisix/admin/configs"` with the condition "count < 1 in 5 minutes".

Useful while investigating an alarm (in the stream's SQL search, not as rules):
```sql
SELECT uri, status, COUNT(*) AS n WHERE status >= 500 GROUP BY uri, status ORDER BY n DESC LIMIT 20
SELECT client_ip, COUNT(*) AS n WHERE status = 429 GROUP BY client_ip ORDER BY n DESC LIMIT 20
SELECT uri, MAX(request_time) AS worst, COUNT(*) AS n WHERE request_time > 1 GROUP BY uri ORDER BY n DESC LIMIT 20
```

## 3. Test each alarm once
Run these from the admin host, then wait for the next evaluation (up to 5 minutes):
```bash
U=https://beta-test.paykoko.pk
# 3: rate-limit spike (each APISIX pod allows 600/min per IP)
seq 1600 | xargs -P30 -I{} curl -s -o /dev/null $U/api/health
# 1: 5xx: scale the orders backend to 0 for 2 minutes (orders API returns 503), then back
kubectl -n sample-app scale deploy/orders-backend --replicas=0; seq 60 | xargs -P5 -I{} curl -s -o /dev/null $U/orders-api/health; sleep 120
kubectl -n sample-app scale deploy/orders-backend --replicas=1
```
Test 1 makes the orders API unavailable for about 2 minutes, so do it in a quiet period.
Test 4 by checking the rule's preview, or on a test cluster. Stopping the controller on beta would hide real problems.
Test 2 is hard to trigger safely; checking the preview is enough.

## Alternative: AOM metric alarms
The same `apisix_*` metrics are already in AOM (ServiceMonitor → CCE Prometheus agent → AOM), and they don't need log structuring.
In AOM → Alarm Rules → Metric/PromQL:
```promql
sum(increase(apisix_http_status{code=~"5.."}[5m])) >= 20                                         # 5xx
histogram_quantile(0.95, sum(rate(apisix_http_latency_bucket{type="request"}[5m])) by (le)) > 1000   # p95 > 1s (ms)
sum(increase(apisix_http_status{code="429"}[5m])) >= 200                                         # 429 spike
absent(apisix_node_info) or count(apisix_node_info) < 2                                         # a gateway pod is gone
```
LTS is still where you read the individual requests behind an alarm.
