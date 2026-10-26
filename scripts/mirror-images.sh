#!/usr/bin/env bash
# Pull pinned upstream images and push them to sky47 SWR.
# Prereq: docker login swr.pk-isb-1.sky47.com (see sample project K8S-DEPLOYMENT.md §3)
source "$(dirname "$0")/lib.sh"
need docker
for src in $IMAGES; do
  name=${src##*/}            # apisix:3.18.0-ubuntu
  dst="$SWR/$name"
  log "mirror $src -> $dst"
  docker pull "$src"
  docker tag "$src" "$dst"
  docker push "$dst"
done
log "done"
