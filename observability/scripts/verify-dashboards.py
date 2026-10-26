#!/usr/bin/env python3
"""Run every panel query of the provisioned dashboards against the live stack.

Catches panels that error (bad PromQL/LogQL/TraceQL) or silently show "No data".
Variables use their default ("All" = the variable's allValue, textbox = its value).
Panels whose description says "Empty when" may legitimately return nothing.

Needs port-forwards (observability/scripts/verify-dashboards.sh starts them):
  PROM=http://localhost:19091 LOKI=http://localhost:13100 TEMPO=http://localhost:13200
Usage: verify-dashboards.py [dashboards dir]      exit 1 if any panel errors or is unexpectedly empty
"""
import glob
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PROM = os.environ.get("PROM", "http://localhost:19091")
LOKI = os.environ.get("LOKI", "http://localhost:13100")
TEMPO = os.environ.get("TEMPO", "http://localhost:13200")
ROOT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "dashboards")


def get(url, params):
    try:
        with urllib.request.urlopen(url + "?" + urllib.parse.urlencode(params), timeout=60) as r:
            return json.load(r), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode()[:200]}"
    except Exception as e:  # noqa: BLE001 - report any failure as a panel error
        return None, str(e)[:200]


def substitute(expr, variables):
    for name, value in variables.items():
        expr = re.sub(r"\$\{" + name + r"(:[a-z]+)?\}|\$" + name + r"\b", value, expr)
    return expr


def defaults(dash):
    out = {}
    for v in dash.get("templating", {}).get("list", []):
        if v["type"] == "textbox":
            out[v["name"]] = v["query"]
        else:
            out[v["name"]] = v.get("allValue") or ".*"
    return out


def check_target(t):
    """returns (status, detail): OK / EMPTY / ERROR"""
    uid = (t.get("datasource") or {}).get("uid")
    now = int(time.time())
    if uid == "prometheus":
        d, err = get(PROM + "/api/v1/query", {"query": t["expr"]})
        if err or d.get("status") != "success":
            return "ERROR", err or d.get("error")
        n = len(d["data"]["result"])
        return ("OK", f"{n} series") if n else ("EMPTY", "no series")
    if uid == "loki":
        expr = t["expr"]
        if expr.lstrip().startswith("{"):  # log query
            d, err = get(LOKI + "/loki/api/v1/query_range",
                         {"query": expr, "start": (now - 3600) * 10**9, "end": now * 10**9, "limit": 5})
        else:  # metric query
            d, err = get(LOKI + "/loki/api/v1/query", {"query": expr, "time": now * 10**9})
        if err or d.get("status") != "success":
            return "ERROR", err or str(d)[:200]
        n = len(d["data"]["result"])
        return ("OK", f"{n} streams/series") if n else ("EMPTY", "no data in the last hour")
    if uid == "tempo":
        if t.get("queryType") == "serviceMap":
            d, err = get(PROM + "/api/v1/query", {"query": "traces_service_graph_request_total"})
            if err:
                return "ERROR", err
            n = len(d["data"]["result"])
            return ("OK", f"{n} edges") if n else ("EMPTY", "no service-graph edges")
        d, err = get(TEMPO + "/api/search", {"q": t["query"], "limit": 20, "start": now - 3600, "end": now})
        if err:
            return "ERROR", err
        n = len(d.get("traces", []))
        return ("OK", f"{n} traces") if n else ("EMPTY", "no matching traces in the last hour")
    return "ERROR", f"unknown datasource {uid}"


def main():
    bad = 0
    for path in sorted(glob.glob(os.path.join(ROOT, "*", "*.json"))):
        dash = json.load(open(path))
        if dash.get("uid") == "apisix-official":
            continue  # upstream dashboard, not ours
        variables = defaults(dash)
        print(f"\n== {os.path.relpath(path, ROOT)}: {dash['title']}")
        for p in dash["panels"]:
            if p["type"] == "row":
                continue
            may_be_empty = "Empty when" in (p.get("description") or "")
            for t in p.get("targets", []):
                t = dict(t)
                for k in ("expr", "query"):
                    if k in t:
                        t[k] = substitute(t[k], variables)
                status, detail = check_target(t)
                if status == "EMPTY" and may_be_empty:
                    status = "ok-empty"
                mark = {"OK": "  ok ", "ok-empty": "  ok ", "EMPTY": "EMPTY", "ERROR": "ERROR"}[status]
                if status in ("EMPTY", "ERROR"):
                    bad += 1
                print(f"  [{mark}] {p['title'][:55]:55} {t.get('refId','')} {detail}")
    print(f"\n{'FAILED' if bad else 'PASSED'}: {bad} panel queries with errors or unexpected empty results")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
