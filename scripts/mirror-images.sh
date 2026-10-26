#!/usr/bin/env bash
# Pull pinned upstream images and push them to sky47 SWR.
# Prereq: docker login swr.pk-isb-1.sky47.com (see sample project K8S-DEPLOYMENT.md §3)
# DOCKER overrides the client, e.g. DOCKER="sudo -u jenkins -H docker" to use
# the jenkins user's SWR login.
source "$(dirname "$0")/lib.sh"
DOCKER=${DOCKER:-docker}
for src in $IMAGES; do
  name=${src##*/}            # apisix:3.18.0-ubuntu
  dst="$SWR/$name"
  log "mirror $src -> $dst"
  $DOCKER pull "$src"
  $DOCKER tag "$src" "$dst"
  $DOCKER push "$dst"
done
log "done"
