#!/usr/bin/env bash
# Port-forward Prometheus/Loki/Tempo and run verify-dashboards.py against them.
source "$(dirname "$0")/common.sh"
need kubectl; need python3
pids=()
for f in "svc/obs-prometheus 19091:80" "svc/loki 13100:3100" "svc/tempo 13200:3200"; do
  # shellcheck disable=SC2086
  kubectl -n "$OBS_NAMESPACE" port-forward $f >/dev/null 2>&1 & pids+=($!)
done
trap 'kill "${pids[@]}" 2>/dev/null' EXIT
until curl -s -o /dev/null localhost:19091/-/ready && curl -s -o /dev/null localhost:13100/ready \
   && curl -s -o /dev/null localhost:13200/ready; do sleep 1; done
python3 -I "$OBS/scripts/verify-dashboards.py" "$OBS/dashboards"
