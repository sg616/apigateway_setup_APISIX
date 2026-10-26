#!/usr/bin/env python3
"""Generate the Grafana dashboards (JSON) for observability/dashboards/<folder>/.

Run after editing:  python3 -I observability/dashboards/build.py
Then check every panel query against the live stack:  observability/scripts/verify-dashboards.py
gateway/apisix-official.json is imported from apache/apisix and not generated here.
Folders map 1:1 to Grafana folders (values/grafana.yaml dashboardProviders).
"""
import json
import os

OUT = os.path.dirname(os.path.abspath(__file__))
P = {"type": "prometheus", "uid": "prometheus"}
L = {"type": "loki", "uid": "loki"}
T = {"type": "tempo", "uid": "tempo"}
APP_ROUTES = 'route=~"sample-app_.*"'          # app traffic only (prefer_name labels)
# APISIX access log: the container also writes nginx [warn]/[error] lines, so keep
# only the JSON access-log lines and drop parse errors before any field filter.
GWJ = '{namespace="apisix", container="apisix"} |= "\\"client_ip\\"" | json | __error__=""'


class Dash:
    def __init__(self, uid, title, description, tags, variables=()):
        self.uid, self.title, self.description, self.tags = uid, title, description, tags
        self.variables, self.panels, self.y, self.x, self.row_h = list(variables), [], 0, 0, 0

    def _add(self, panel, w, h):
        if self.x + w > 24:
            self.y, self.x, self.row_h = self.y + self.row_h, 0, 0
        panel.update(id=len(self.panels) + 1, gridPos={"x": self.x, "y": self.y, "w": w, "h": h})
        self.panels.append(panel)
        self.x += w
        self.row_h = max(self.row_h, h)
        return panel

    def row(self, title):
        if self.x:
            self.y, self.x, self.row_h = self.y + self.row_h, 0, 0
        self.panels.append({"id": len(self.panels) + 1, "type": "row", "title": title, "collapsed": False,
                            "gridPos": {"x": 0, "y": self.y, "w": 24, "h": 1}, "panels": []})
        self.y += 1

    def stat(self, title, expr, unit, w=6, thresholds=None, decimals=None, desc=None):
        steps = [{"color": "green", "value": None}] + [{"color": c, "value": v} for v, c in (thresholds or [])]
        return self._add({"type": "stat", "title": title, "description": desc, "datasource": P,
                          "targets": [{"refId": "A", "datasource": P, "expr": expr, "instant": True}],
                          "fieldConfig": {"defaults": {"unit": unit, "decimals": decimals,
                                                       "thresholds": {"mode": "absolute", "steps": steps}}, "overrides": []},
                          "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "background", "graphMode": "area"}}, w, 4)

    def gauge(self, title, expr, unit, w=6, thresholds=None, minv=0, maxv=100, desc=None):
        steps = [{"color": "green", "value": None}] + [{"color": c, "value": v} for v, c in (thresholds or [])]
        return self._add({"type": "gauge", "title": title, "description": desc, "datasource": P,
                          "targets": [{"refId": "A", "datasource": P, "expr": expr, "instant": True}],
                          "fieldConfig": {"defaults": {"unit": unit, "min": minv, "max": maxv,
                                                       "thresholds": {"mode": "absolute", "steps": steps}}, "overrides": []},
                          "options": {"reduceOptions": {"calcs": ["lastNotNull"]}}}, w, 6)

    def ts(self, title, targets, unit, w=12, h=8, ds=P, desc=None, stack=False):
        return self._add({"type": "timeseries", "title": title, "description": desc, "datasource": ds,
                          "targets": [{"refId": chr(65 + i), "datasource": ds, "expr": e, "legendFormat": lf}
                                      for i, (e, lf) in enumerate(targets)],
                          "fieldConfig": {"defaults": {"unit": unit, "custom": {"stacking": {"mode": "normal" if stack else "none"}}},
                                          "overrides": []},
                          "options": {"legend": {"displayMode": "table", "placement": "right", "calcs": ["mean", "max", "lastNotNull"]}}}, w, h)

    def table(self, title, targets, w=24, h=8, ds=P, desc=None, units=None):
        overrides = [{"matcher": {"id": "byName", "options": name},
                      "properties": [{"id": "unit", "value": u}]} for name, u in (units or {}).items()]
        return self._add({"type": "table", "title": title, "description": desc, "datasource": ds,
                          "targets": [{"refId": chr(65 + i), "datasource": ds, "expr": e, "instant": True,
                                       "format": "table", "legendFormat": lf} for i, (e, lf) in enumerate(targets)],
                          "transformations": [{"id": "merge"}, {"id": "organize", "options": {"excludeByName": {"Time": True}}}],
                          "fieldConfig": {"defaults": {}, "overrides": overrides}}, w, h)

    def logs(self, title, expr, w=24, h=10, desc=None):
        return self._add({"type": "logs", "title": title, "description": desc, "datasource": L,
                          "targets": [{"refId": "A", "datasource": L, "expr": expr}],
                          "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending",
                                      "enableLogDetails": True, "dedupStrategy": "none"}}, w, h)

    def traces(self, title, traceql, w=24, h=8, desc=None):
        return self._add({"type": "table", "title": title, "description": desc, "datasource": T,
                          "targets": [{"refId": "A", "datasource": T, "queryType": "traceql", "query": traceql,
                                       "limit": 20, "tableType": "traces"}]}, w, h)

    def nodegraph(self, title, w=24, h=10, desc=None):
        return self._add({"type": "nodeGraph", "title": title, "description": desc, "datasource": T,
                          "targets": [{"refId": "A", "datasource": T, "queryType": "serviceMap"}]}, w, h)

    def write(self, folder):
        d = {"uid": self.uid, "title": self.title, "description": self.description, "tags": self.tags,
             "timezone": "browser", "schemaVersion": 39, "version": 1, "refresh": "30s", "editable": False,
             "time": {"from": "now-1h", "to": "now"}, "templating": {"list": self.variables}, "panels": self.panels}
        path = os.path.join(OUT, folder, f"{self.uid}.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(d, f, indent=1)
        return path


def query_var(name, label, query, ds=P, include_all=True):
    return {"name": name, "label": label, "type": "query", "datasource": ds, "query": query, "refresh": 2,
            "includeAll": include_all, "multi": True, "allValue": ".*", "current": {"text": "All", "value": "$__all"}}


def text_var(name, label, value):
    return {"name": name, "label": label, "type": "textbox", "query": value,
            "current": {"text": value, "value": value}}


def red(v_orange, v_red):
    return [(v_orange, "orange"), (v_red, "red")]


GEN_NOTE = "Generated by observability/dashboards/build.py; edit there, not in the UI."

# ---------------------------------------------------------------- Gateway / overview
d = Dash("gateway-overview", "Gateway overview: metrics, logs, traces",
         "One screen for the APISIX gateway and the sample app. " + GEN_NOTE, ["apisix", "gateway"])
d.stat("Requests / s", "sum(rate(apisix_http_status[5m]))", "reqps", decimals=1)
d.stat("5xx share", '100 * sum(rate(apisix_http_status{code=~"5.."}[5m])) / clamp_min(sum(rate(apisix_http_status[5m])), 1e-9)',
       "percent", thresholds=red(1, 5), decimals=2)
d.stat("429 / min (rate limited)", '60 * sum(rate(apisix_http_status{code="429"}[5m])) or vector(0)', "short", thresholds=red(10, 100), decimals=0)
d.stat("p95 latency", 'histogram_quantile(0.95, sum by (le) (rate(apisix_http_latency_bucket{type="request"}[5m])))', "ms",
       thresholds=red(500, 1000), decimals=0)
d.ts("Requests / s by route", [("sum by (route) (rate(apisix_http_status[5m]))", "{{route}}")], "reqps")
d.ts("Responses by status code", [("sum by (code) (rate(apisix_http_status[5m]))", "{{code}}")], "reqps")
d.ts("p95 latency by service (from traces)", [('histogram_quantile(0.95, sum by (le, service) (rate(traces_spanmetrics_latency_bucket{span_kind="SPAN_KIND_SERVER"}[5m])))', "{{service}}")], "s")
d.ts("Errors by service (from traces)", [('sum by (service) (rate(traces_spanmetrics_calls_total{status_code="STATUS_CODE_ERROR"}[5m])) or vector(0)', "{{service}}")], "reqps")
d.logs("Gateway errors: 5xx responses (click trace_id for the trace)", GWJ + ' | status >= 500', w=12, h=9,
       desc="Empty when the gateway returned no 5xx in the time range.")
d.logs("App errors and warnings (sample-app)", '{namespace="sample-app"} |~ "(?i)\\\\b(ERROR|WARN|Exception)\\\\b"', w=12, h=9,
       desc="Empty when the app logged no ERROR/WARN in the time range: that's good.")
d.traces("Recent slow or failed traces (click to open)", "{ duration > 500ms || status = error }")
d.ts("Memory by pod (working set)", [('sum by (pod) (container_memory_working_set_bytes{namespace=~"sample-app|apisix|observability", container!="", container!="POD"})', "{{pod}}")], "bytes")
d.ts("Container restarts (1h)", [('sum by (namespace, pod) (increase(kube_pod_container_status_restarts_total{namespace=~"sample-app|apisix|observability"}[1h]))', "{{namespace}}/{{pod}}")], "short")
d.write("gateway")

# ---------------------------------------------------------------- Gateway / SLO
slo_vars = [text_var("slo", "Availability SLO %", "99.5"), text_var("slo_p95", "p95 latency target ms", "500")]
err = lambda w: f'sum(rate(apisix_http_status{{{APP_ROUTES}, code=~"5.."}}[{w}]))'
tot = lambda w: f'sum(rate(apisix_http_status{{{APP_ROUTES}}}[{w}]))'
avail = lambda w: f"100 * (1 - (({err(w)}) or vector(0)) / clamp_min({tot(w)}, 1e-9))"
burn = lambda w: f"((({err(w)}) or vector(0)) / clamp_min({tot(w)}, 1e-9)) / (1 - $slo/100)"
d = Dash("gateway-slo", "Gateway SLO: availability and latency",
         "Availability = share of app requests (routes sample-app_*) that are not 5xx. Edit the targets in the "
         "variables above. Burn rate 1 = spending the error budget exactly on schedule; >14 on 1h = page-worthy. "
         "Data before 2026-10-26 19:06 UTC used hash route ids and is not counted. " + GEN_NOTE,
         ["apisix", "slo"], slo_vars)
d.row("Availability")
for w in ("1h", "1d", "7d"):
    d.stat(f"Availability ({w})", avail(w), "percent", w=6, decimals=3,
           thresholds=[(0, "red"), (99, "orange"), (99.5, "green")])
d.stat("Error budget left (7d)", f"100 * (1 - ((({err('7d')}) or vector(0)) / clamp_min({tot('7d')}, 1e-9)) / (1 - $slo/100))",
       "percent", w=6, decimals=1, thresholds=[(0, "red"), (25, "orange"), (50, "green")],
       desc="100% = no budget used; 0% = SLO breached for the 7-day window.")
d.ts("Error-budget burn rate", [(burn("1h"), "1h"), (burn("6h"), "6h"), ("vector(1)", "budget pace (1)")], "short",
     desc="How fast the error budget is used. 1 = on pace; sustained >1 breaches the SLO before the window ends.")
d.ts("Availability over time (5m)", [(avail("5m"), "availability %"), ("vector($slo)", "SLO")], "percent")
d.row("Latency")
d.stat("p95 latency now", f'histogram_quantile(0.95, sum by (le) (rate(apisix_http_latency_bucket{{type="request", {APP_ROUTES}}}[5m])))',
       "ms", w=6, decimals=0, thresholds=[(0, "green"), (500, "red")])
d.stat("Requests under target (1d)",
       f'100 * sum(rate(apisix_http_latency_bucket{{type="request", {APP_ROUTES}, le="500.0"}}[1d])) / clamp_min(sum(rate(apisix_http_latency_count{{type="request", {APP_ROUTES}}}[1d])), 1e-9)',
       "percent", w=6, decimals=2, desc="Share of requests faster than 500 ms (nearest histogram bucket to the default target).")
d.ts("p95 latency vs target", [(f'histogram_quantile(0.95, sum by (le) (rate(apisix_http_latency_bucket{{type="request", {APP_ROUTES}}}[5m])))', "p95"),
                               ("vector($slo_p95)", "target")], "ms", w=12)
d.row("Per route (1d)")
d.table("Per-route availability and latency (1d)",
        [(f'100 * (1 - (sum by (route) (rate(apisix_http_status{{{APP_ROUTES}, code=~"5.."}}[1d])) or (sum by (route) (rate(apisix_http_status{{{APP_ROUTES}}}[1d])) * 0)) / clamp_min(sum by (route) (rate(apisix_http_status{{{APP_ROUTES}}}[1d])), 1e-9))', "availability %"),
         (f'histogram_quantile(0.95, sum by (le, route) (rate(apisix_http_latency_bucket{{type="request", {APP_ROUTES}}}[1d])))', "p95 ms"),
         (f'sum by (route) (increase(apisix_http_status{{{APP_ROUTES}}}[1d]))', "requests")],
        units={"Value #A": "percent", "Value #B": "ms", "Value #C": "short"})
d.write("gateway")

# ---------------------------------------------------------------- Services / RED
svc_var = query_var("service", "Service", "label_values(traces_spanmetrics_calls_total, service)")
S = 'service=~"$service"'
d = Dash("services-red", "Services: rate, errors, duration (from traces)",
         "RED metrics derived from traces by Tempo's metrics-generator (span metrics + service graph). " + GEN_NOTE,
         ["services", "traces"], [svc_var])
d.stat("Requests / s", f'sum(rate(traces_spanmetrics_calls_total{{{S}, span_kind="SPAN_KIND_SERVER"}}[5m]))', "reqps", decimals=2)
d.stat("Error %", f'100 * (sum(rate(traces_spanmetrics_calls_total{{{S}, span_kind="SPAN_KIND_SERVER", status_code="STATUS_CODE_ERROR"}}[5m])) or vector(0)) / clamp_min(sum(rate(traces_spanmetrics_calls_total{{{S}, span_kind="SPAN_KIND_SERVER"}}[5m])), 1e-9)',
       "percent", decimals=2, thresholds=red(1, 5))
d.stat("p95", f'histogram_quantile(0.95, sum by (le) (rate(traces_spanmetrics_latency_bucket{{{S}, span_kind="SPAN_KIND_SERVER"}}[5m])))', "s", decimals=3, thresholds=red(0.5, 1))
d.stat("DB query p95", f'histogram_quantile(0.95, sum by (le) (rate(traces_spanmetrics_latency_bucket{{{S}, span_kind="SPAN_KIND_CLIENT"}}[5m])))', "s", decimals=4, thresholds=red(0.1, 0.5))
d.ts("Requests / s by service", [(f'sum by (service) (rate(traces_spanmetrics_calls_total{{{S}, span_kind="SPAN_KIND_SERVER"}}[5m]))', "{{service}}")], "reqps")
d.ts("Latency p50 / p95 / p99 by service", [(f'histogram_quantile({q}, sum by (le, service) (rate(traces_spanmetrics_latency_bucket{{{S}, span_kind="SPAN_KIND_SERVER"}}[5m])))', f"{{{{service}}}} p{int(q*100)}")
                                             for q in (0.5, 0.95, 0.99)], "s")
d.table("Endpoints (5m): rate, errors, p95",
        [(f'sum by (service, span_name) (rate(traces_spanmetrics_calls_total{{{S}, span_kind="SPAN_KIND_SERVER"}}[5m]))', "req/s"),
         (f'sum by (service, span_name) (rate(traces_spanmetrics_calls_total{{{S}, span_kind="SPAN_KIND_SERVER", status_code="STATUS_CODE_ERROR"}}[5m])) or (sum by (service, span_name) (rate(traces_spanmetrics_calls_total{{{S}, span_kind="SPAN_KIND_SERVER"}}[5m])) * 0)', "errors/s"),
         (f'histogram_quantile(0.95, sum by (le, service, span_name) (rate(traces_spanmetrics_latency_bucket{{{S}, span_kind="SPAN_KIND_SERVER"}}[5m])))', "p95")],
        units={"Value #A": "reqps", "Value #B": "reqps", "Value #C": "s"})
d.table("Database queries (5m): rate and p95 by statement",
        [(f'sum by (service, span_name) (rate(traces_spanmetrics_calls_total{{{S}, span_kind="SPAN_KIND_CLIENT"}}[5m]))', "calls/s"),
         (f'histogram_quantile(0.95, sum by (le, service, span_name) (rate(traces_spanmetrics_latency_bucket{{{S}, span_kind="SPAN_KIND_CLIENT"}}[5m])))', "p95")],
        units={"Value #A": "reqps", "Value #B": "s"}, h=6)
d.nodegraph("Service graph (user → apisix; backends → MySQL)",
            desc="APISIX emits only a SERVER span, so there's no apisix → backend edge; traces still link them.")
d.ts("Service graph calls / s", [('sum by (client, server) (rate(traces_service_graph_request_total[5m]))', "{{client}} → {{server}}")], "reqps")
d.ts("Service graph failed calls / s", [('sum by (client, server) (rate(traces_service_graph_request_failed_total[5m])) or vector(0)', "{{client}} → {{server}}")], "reqps")
d.traces("Recent error traces", "{ status = error }", h=7, desc="Empty when nothing failed recently: that's good.")
d.traces("Slowest recent traces (> 300 ms)", "{ duration > 300ms }", h=7)
d.write("services")

# ---------------------------------------------------------------- Services / JVM
jvm_var = query_var("service", "Service", "label_values(jvm_memory_used_bytes, service_name)")
J = 'service_name=~"$service"'
d = Dash("jvm-spring-boot", "JVM / Spring Boot",
         "From the OpenTelemetry Java agent (OTLP → Prometheus). No app code involved. " + GEN_NOTE,
         ["services", "jvm"], [jvm_var])
d.stat("Heap used", f'sum(jvm_memory_used_bytes{{{J}, jvm_memory_type="heap"}})', "bytes")
d.stat("Heap used % of max", f'100 * sum(jvm_memory_used_bytes{{{J}, jvm_memory_type="heap"}}) / sum(jvm_memory_limit_bytes{{{J}, jvm_memory_type="heap"}})',
       "percent", decimals=0, thresholds=red(75, 90))
d.stat("GC time %", f'100 * sum(rate(jvm_gc_duration_seconds_sum{{{J}}}[5m]))', "percent", decimals=2, thresholds=red(5, 10),
       desc="Share of wall time spent in GC.")
d.stat("Threads", f'sum(jvm_thread_count{{{J}}})', "short", decimals=0)
d.ts("Heap used by service", [(f'sum by (service_name) (jvm_memory_used_bytes{{{J}, jvm_memory_type="heap"}})', "{{service_name}} used"),
                              (f'sum by (service_name) (jvm_memory_limit_bytes{{{J}, jvm_memory_type="heap"}})', "{{service_name}} max")], "bytes")
d.ts("Memory by pool", [(f'sum by (service_name, jvm_memory_pool_name) (jvm_memory_used_bytes{{{J}}})', "{{service_name}} {{jvm_memory_pool_name}}")], "bytes")
d.ts("GC pauses / s", [(f'sum by (service_name, jvm_gc_name) (rate(jvm_gc_duration_seconds_count{{{J}}}[5m]))', "{{service_name}} {{jvm_gc_name}}")], "short")
d.ts("GC pause time / s", [(f'sum by (service_name, jvm_gc_name) (rate(jvm_gc_duration_seconds_sum{{{J}}}[5m]))', "{{service_name}} {{jvm_gc_name}}")], "s")
d.ts("CPU utilization (JVM)", [(f'avg by (service_name) (jvm_cpu_recent_utilization_ratio{{{J}}})', "{{service_name}}")], "percentunit")
d.ts("Threads by state", [(f'sum by (service_name, jvm_thread_state) (jvm_thread_count{{{J}}})', "{{service_name}} {{jvm_thread_state}}")], "short")
d.row("Database connection pool (HikariCP)")
d.ts("Connections used / idle", [(f'sum by (pool_name, state) (db_client_connections_usage{{{J}}})', "{{pool_name}} {{state}}"),
                                 (f'sum by (pool_name) (db_client_connections_max{{{J}}})', "{{pool_name}} max")], "short")
d.ts("Pending connection requests", [(f'sum by (pool_name) (db_client_connections_pending_requests{{{J}}})', "{{pool_name}}")], "short")
d.ts("Connection wait p95", [(f'histogram_quantile(0.95, sum by (le, pool_name) (rate(db_client_connections_wait_time_milliseconds_bucket{{{J}}}[5m])))', "{{pool_name}}")], "ms")
d.ts("Connection use time p95", [(f'histogram_quantile(0.95, sum by (le, pool_name) (rate(db_client_connections_use_time_milliseconds_bucket{{{J}}}[5m])))', "{{pool_name}}")], "ms")
d.row("HTTP server (Spring)")
d.ts("Requests / s by route", [(f'sum by (service_name, http_route) (rate(http_server_request_duration_seconds_count{{{J}}}[5m]))', "{{service_name}} {{http_route}}")], "reqps")
d.ts("p95 by route", [(f'histogram_quantile(0.95, sum by (le, service_name, http_route) (rate(http_server_request_duration_seconds_bucket{{{J}}}[5m])))', "{{service_name}} {{http_route}}")], "s")
d.ts("Responses by status", [(f'sum by (service_name, http_response_status_code) (rate(http_server_request_duration_seconds_count{{{J}}}[5m]))', "{{service_name}} {{http_response_status_code}}")], "reqps", w=24)
d.write("services")

# ---------------------------------------------------------------- Services / Logs
ns_var = {"name": "namespace", "label": "Namespace", "type": "custom", "query": "sample-app,apisix,observability",
          "includeAll": True, "multi": True, "allValue": "sample-app|apisix|observability",
          "current": {"text": "All", "value": "$__all"}}
app_var = query_var("app", "App", 'label_values({namespace=~"$namespace"}, app)', ds=L)
SEL = '{namespace=~"$namespace", app=~"$app"}'
GW = GWJ
d = Dash("logs-explorer", "Logs explorer",
         "Loki logs of sample-app, apisix and observability. Gateway panels parse the APISIX JSON access log. " + GEN_NOTE,
         ["logs"], [ns_var, app_var])
d.ts("Log lines / s by app", [(f'sum by (app) (rate({SEL}[1m]))', "{{app}}")], "short", ds=L, stack=True)
d.ts("ERROR / WARN lines per minute", [(f'sum by (app) (count_over_time({SEL} |~ "(?i)\\\\b(ERROR|Exception)\\\\b" [1m])) or vector(0)', "{{app}} error"),
                                       (f'sum by (app) (count_over_time({SEL} |~ "\\\\bWARN\\\\b" [1m])) or vector(0)', "{{app}} warn")], "short", ds=L)
d.table("Top error messages (1h)",
        [(f'topk(15, sum by (app, msg) (count_over_time({SEL} |~ "\\\\b(ERROR|WARN)\\\\b" | regexp "(?P<msg>(ERROR|WARN).{{0,140}})" [1h])))', "count")],
        ds=L, h=9, desc="Spring Boot lines grouped by the first 140 characters from the level on.")
d.row("Gateway access log")
d.ts("Gateway 4xx / 5xx by path", [(f'sum by (path, status) (count_over_time({GW} | status >= 400 [5m]))', "{{status}} {{path}}")], "short", ds=L)
d.table("Top client IPs (1h)", [(f'topk(10, sum by (client_ip) (count_over_time({GW} [1h])))', "requests")], ds=L, w=12, h=8)
d.table("Slowest paths (1h): requests over 1 s", [(f'topk(10, sum by (path) (count_over_time({GW} | request_time > 1 [1h])))', "slow requests")],
        ds=L, w=12, h=8, desc="Empty when nothing took longer than 1 s.")
d.logs("Slow gateway requests (> 1 s)", f'{GW} | request_time > 1', h=8)
d.logs("Live logs (filtered by the variables)", SEL, h=14)
d.write("services")

# ---------------------------------------------------------------- Platform / Kubernetes
NS = 'namespace=~"sample-app|apisix|observability"'
d = Dash("kubernetes-node", "Kubernetes and node",
         "Node (node-exporter), workloads (kube-state-metrics) and containers (cAdvisor) for the app, gateway and "
         "observability namespaces. " + GEN_NOTE, ["platform", "kubernetes"])
d.gauge("Node CPU used", '100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m])))', "percent", thresholds=red(70, 90))
d.gauge("Node memory used", '100 * (1 - sum(node_memory_MemAvailable_bytes) / sum(node_memory_MemTotal_bytes))', "percent", thresholds=red(75, 90))
d.gauge("Root disk used", '100 * (1 - sum(node_filesystem_avail_bytes{mountpoint="/"}) / sum(node_filesystem_size_bytes{mountpoint="/"}))', "percent", thresholds=red(75, 90))
d.stat("Pods not Running (3 ns)", f'sum(kube_pod_status_phase{{{NS}, phase=~"Pending|Failed|Unknown"}}) or vector(0)', "short", thresholds=[(1, "red")], decimals=0)
d.ts("Node CPU by mode", [('sum by (mode) (rate(node_cpu_seconds_total{mode!="idle"}[5m]))', "{{mode}}")], "short", stack=True)
d.ts("Node network", [('sum(rate(node_network_receive_bytes_total{device!~"lo|veth.*|cali.*|tunl.*"}[5m]))', "receive"),
                      ('sum(rate(node_network_transmit_bytes_total{device!~"lo|veth.*|cali.*|tunl.*"}[5m]))', "transmit")], "Bps")
d.row("Workloads")
d.table("Deployments: desired vs available",
        [(f'sum by (namespace, deployment) (kube_deployment_spec_replicas{{{NS}}})', "desired"),
         (f'sum by (namespace, deployment) (kube_deployment_status_replicas_available{{{NS}}})', "available")], h=7)
d.ts("Container restarts (1h)", [(f'sum by (namespace, pod) (increase(kube_pod_container_status_restarts_total{{{NS}}}[1h]))', "{{namespace}}/{{pod}}")], "short")
d.ts("Pods by phase", [(f'sum by (namespace, phase) (kube_pod_status_phase{{{NS}}}) > 0', "{{namespace}} {{phase}}")], "short")
d.row("Containers vs limits (the Grafana OOM would show here first)")
d.ts("Memory used % of limit", [(f'100 * max by (namespace, pod, container) (container_memory_working_set_bytes{{{NS}, container!="", container!="POD"}}) / on (namespace, pod, container) group_left max by (namespace, pod, container) (kube_pod_container_resource_limits{{{NS}, resource="memory"}})', "{{pod}}/{{container}}")],
     "percent", desc="Above ~90% the container is close to an OOM kill.")
d.ts("CPU used % of limit", [(f'100 * sum by (namespace, pod, container) (rate(container_cpu_usage_seconds_total{{{NS}, container!="", container!="POD"}}[5m])) / on (namespace, pod, container) group_left max by (namespace, pod, container) (kube_pod_container_resource_limits{{{NS}, resource="cpu"}})', "{{pod}}/{{container}}")], "percent")
d.ts("CPU throttling %", [(f'100 * sum by (pod, container) (rate(container_cpu_cfs_throttled_periods_total{{{NS}, container!=""}}[5m])) / clamp_min(sum by (pod, container) (rate(container_cpu_cfs_periods_total{{{NS}, container!=""}}[5m])), 1e-9)', "{{pod}}/{{container}}")], "percent")
d.ts("Memory working set by pod", [(f'sum by (namespace, pod) (container_memory_working_set_bytes{{{NS}, container!="", container!="POD"}})', "{{namespace}}/{{pod}}")], "bytes")
d.write("platform")

# ---------------------------------------------------------------- Platform / stack health
d = Dash("observability-health", "Observability stack health",
         "Is the monitoring itself OK? Scrapes, ingestion of metrics/logs/traces, disk use. Alerting lives in "
         "AOM/LTS (docs/lts-alarms.md), because this stack shares the node with the app. " + GEN_NOTE, ["platform", "observability"])
d.stat("Scrape targets down", 'count(up == 0) or vector(0)', "short", thresholds=[(1, "red")], decimals=0)
d.stat("Prometheus series", "prometheus_tsdb_head_series", "short", decimals=0)
d.stat("Log lines / s into Loki", "sum(rate(loki_distributor_lines_received_total[5m]))", "short", decimals=1)
d.stat("Spans / s into Tempo", "sum(rate(tempo_distributor_spans_received_total[5m]))", "short", decimals=1)
d.ts("Scrape targets", [("sum by (job) (up)", "{{job}} up"), ("sum by (job) (up == bool 0)", "{{job}} down")], "short")
d.ts("Prometheus samples appended / s", [("rate(prometheus_tsdb_head_samples_appended_total[5m])", "samples/s")], "short")
d.ts("Loki ingestion", [("sum(rate(loki_distributor_bytes_received_total[5m]))", "bytes/s"),
                        ("sum(rate(loki_discarded_samples_total[5m])) or vector(0)", "discarded/s")], "short")
d.ts("Tempo ingestion", [("sum(rate(tempo_distributor_spans_received_total[5m]))", "spans/s"),
                         ("sum(rate(tempo_discarded_spans_total[5m])) or vector(0)", "discarded/s")], "short")
d.ts("Alloy: lines read vs dropped", [("sum(rate(loki_source_file_read_lines_total[5m]))", "read/s"),
                                      ('sum by (reason) (rate(loki_process_dropped_lines_total[5m])) or vector(0)', "dropped/s {{reason}}"),
                                      ("sum(rate(loki_write_dropped_entries_total[5m])) or vector(0)", "write dropped/s")], "short",
     desc="'apisix_admin_push' drops are intended (controller config pushes).")
d.ts("Grafana requests / s", [('sum by (status_code) (rate(grafana_http_request_duration_seconds_count[5m]))', "{{status_code}}")], "reqps")
d.row("Storage")
d.gauge("PVC used % (max)", "100 * max(kubelet_volume_stats_used_bytes / kubelet_volume_stats_capacity_bytes)", "percent", thresholds=red(75, 90))
d.table("PVC usage", [('100 * kubelet_volume_stats_used_bytes{namespace="observability"} / kubelet_volume_stats_capacity_bytes{namespace="observability"}', "used %"),
                      ('kubelet_volume_stats_capacity_bytes{namespace="observability"}', "capacity")],
        w=18, h=6, units={"Value #A": "percent", "Value #B": "bytes"})
d.ts("PVC used bytes over time", [('kubelet_volume_stats_used_bytes{namespace="observability"}', "{{persistentvolumeclaim}}")], "bytes", w=24)
d.write("platform")

# ---------------------------------------------------------------- size check (ConfigMap limit 1 MiB per folder)
for folder in ("gateway", "services", "platform"):
    size = sum(os.path.getsize(os.path.join(OUT, folder, f)) for f in os.listdir(os.path.join(OUT, folder)))
    assert size < 900_000, f"{folder}: {size} bytes, too close to the 1 MiB ConfigMap limit"
    print(f"{folder}: {sorted(os.listdir(os.path.join(OUT, folder)))} ({size // 1024} KiB)")
