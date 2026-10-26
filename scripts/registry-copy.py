#!/usr/bin/env python3
"""Copy one platform of a public image straight from its registry to SWR.

No local Docker involved: the manifest and every blob are copied by digest,
so the result is byte-identical to upstream. Fallback for mirror-images.sh
when Docker's containerd image store can't push an image (tiny shared layers
whose compressed blobs were garbage-collected, error "does not provide the
specified platform" / "content digest ... not found").

Usage:
  SWR_USER=<region>@<AK> SWR_PASS=<hmac-password> \
    scripts/registry-copy.py grafana/tempo:3.1.0 swr.pk-isb-1.sky47.com/pk-beta-repo/tempo:3.1.0
SWR_USER/SWR_PASS are the same values swr-login.sh derives from the AK/SK.
PLATFORM (default linux/amd64) picks the platform from a multi-arch index.
"""
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

MANIFEST_TYPES = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])
INDEX_TYPES = ("application/vnd.oci.image.index.v1+json",
               "application/vnd.docker.distribution.manifest.list.v2+json")


def parse(ref):
    """'grafana/tempo:3.1.0' -> (registry, repo, tag)"""
    name, _, tag = ref.rpartition(":")
    first = name.split("/")[0]
    if "." in first or ":" in first:
        registry, repo = first, name[len(first) + 1:]
    else:
        registry, repo = "registry-1.docker.io", name if "/" in name else "library/" + name
    return registry, repo, tag


class Registry:
    def __init__(self, host, repo, user=None, password=None, push=False):
        self.base = f"https://{host}/v2/{repo}"
        self.repo, self.user, self.password = repo, user, password
        self.scope = f"repository:{repo}:{'pull,push' if push else 'pull'}"
        self.auth = None
        self._login(host)

    def _login(self, host):
        try:
            urllib.request.urlopen(f"https://{host}/v2/", timeout=30)
            return
        except urllib.error.HTTPError as e:
            if e.code != 401:
                raise
            challenge = e.headers.get("WWW-Authenticate", "")
        basic = None
        if self.user:
            basic = "Basic " + base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        if challenge.lower().startswith("basic"):
            self.auth = basic
            return
        params = dict(re.findall(r'(\w+)="([^"]*)"', challenge))
        url = params["realm"] + "?" + urllib.parse.urlencode(
            {"service": params.get("service", ""), "scope": self.scope})
        req = urllib.request.Request(url, headers={"Authorization": basic} if basic else {})
        tok = json.load(urllib.request.urlopen(req, timeout=30))
        self.auth = "Bearer " + (tok.get("token") or tok["access_token"])

    def req(self, method, url, data=None, headers=None):
        h = dict(headers or {})
        if self.auth:
            h["Authorization"] = self.auth
        if not url.startswith("http"):
            url = self.base + url
        r = urllib.request.Request(url, data=data, headers=h, method=method)
        return urllib.request.urlopen(r, timeout=300)


def main(src_ref, dst_ref):
    platform = os.environ.get("PLATFORM", "linux/amd64")
    os_, arch = platform.split("/")[:2]
    s_host, s_repo, s_tag = parse(src_ref)
    d_host, d_repo, d_tag = parse(dst_ref)
    src = Registry(s_host, s_repo)
    dst = Registry(d_host, d_repo, os.environ["SWR_USER"], os.environ["SWR_PASS"], push=True)

    resp = src.req("GET", f"/manifests/{s_tag}", headers={"Accept": MANIFEST_TYPES})
    body, mtype = resp.read(), resp.headers["Content-Type"].split(";")[0]
    if mtype in INDEX_TYPES:
        idx = json.loads(body)
        digest = next(m["digest"] for m in idx["manifests"]
                      if m.get("platform", {}).get("os") == os_
                      and m.get("platform", {}).get("architecture") == arch)
        resp = src.req("GET", f"/manifests/{digest}", headers={"Accept": MANIFEST_TYPES})
        body, mtype = resp.read(), resp.headers["Content-Type"].split(";")[0]
    manifest = json.loads(body)

    blobs = [manifest["config"]] + manifest["layers"]
    for b in blobs:
        d = b["digest"]
        try:
            dst.req("HEAD", f"/blobs/{d}")
            print(f"  exists {d[:19]} ({b['size']} B)")
            continue
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
        data = src.req("GET", f"/blobs/{d}").read()
        loc = dst.req("POST", "/blobs/uploads/").headers["Location"]
        if not loc.startswith("http"):
            loc = f"https://{d_host}{loc}"
        sep = "&" if "?" in loc else "?"
        dst.req("PUT", f"{loc}{sep}digest={urllib.parse.quote(d)}", data=data,
                headers={"Content-Type": "application/octet-stream"})
        print(f"  copied {d[:19]} ({b['size']} B)")

    r = dst.req("PUT", f"/manifests/{d_tag}", data=body, headers={"Content-Type": mtype})
    print(f"{dst_ref}: digest: {r.headers.get('Docker-Content-Digest')} ({platform})")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
