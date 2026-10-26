#!/usr/bin/env bash
# Long-term SWR login from an IAM access key (AK/SK), for mirror-images.sh.
# Password = hex(HMAC-SHA256(key=SK, msg=AK)), user = <region>@<AK>; it is
# passed on stdin, so it never shows in ps or shell history.
#   AK/SK: env vars AK and SK, or prompted (SK hidden).
#   DOCKER overrides the client, e.g. DOCKER="sudo -u jenkins -H docker".
# Log out afterwards: $DOCKER logout <swr-host>
source "$(dirname "$0")/lib.sh"
need openssl
DOCKER=${DOCKER:-docker}
host=${SWR%%/*}                       # swr.pk-isb-1.sky47.com
region=${host#swr.}; region=${region%%.*}   # pk-isb-1
[[ -n ${AK:-} ]] || read -rp "Access Key Id: " AK
[[ -n ${SK:-} ]] || { read -rsp "Secret Access Key: " SK; echo; }
SWR_USER="$region@$AK"
SWR_PASS=$(printf '%s' "$AK" | openssl dgst -binary -sha256 -hmac "$SK" | od -An -vtx1 | tr -d ' \n')
printf '%s' "$SWR_PASS" | $DOCKER login -u "$SWR_USER" --password-stdin "$host"
unset SK
# Sourced (". scripts/swr-login.sh") it also leaves SWR_USER/SWR_PASS set for the
# registry-copy fallback in mirror-images.sh. Unset them when done.
export SWR_USER SWR_PASS
