#!/usr/bin/env bash
# Pull pinned upstream images and push them to sky47 SWR.
# Prereq: docker login swr.pk-isb-1.sky47.com (see sample project K8S-DEPLOYMENT.md §3)
# DOCKER overrides the client, e.g. DOCKER="sudo -u jenkins -H docker" to use
# the jenkins user's SWR login.
source "$(dirname "$0")/lib.sh"
DOCKER=${DOCKER:-docker}
# Pull and push one platform only (CCE nodes are amd64). Pushing a whole
# multi-arch index fails with Docker's containerd image store when some
# referenced content isn't local ("does not provide any platform" / NotFound).
PLATFORM=${PLATFORM:-linux/amd64}
for src in $IMAGES; do
  name=${src##*/}            # apisix:3.18.0-ubuntu
  dst="$SWR/$name"
  log "mirror $src -> $dst"
  push_one() {
    $DOCKER pull --platform "$PLATFORM" "$src" && $DOCKER tag "$src" "$dst" \
      && $DOCKER push --platform "$PLATFORM" "$dst"
  }
  # A copy pulled earlier without --platform can be an incomplete index:
  # drop the local copies once and pull clean.
  push_one && continue
  log "retry $src after removing local copies"; $DOCKER rmi "$dst" "$src" >/dev/null 2>&1 || true
  push_one && continue
  # Last resort: copy registry-to-registry, bypassing local Docker (needs SWR_USER/SWR_PASS,
  # the same values swr-login.sh derives; see scripts/registry-copy.py)
  [[ -n ${SWR_USER:-} && -n ${SWR_PASS:-} ]] || die "push of $src failed; set SWR_USER/SWR_PASS to use scripts/registry-copy.py"
  log "fallback: registry-copy $src"; python3 -I "$ROOT/scripts/registry-copy.py" "$src" "$dst"
done
log "done"
