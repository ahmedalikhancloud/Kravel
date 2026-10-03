"""Credential-free public image research, never an image pull or cluster write.

Only vetted public repositories on Docker Hub. Fixed host, pinned public DNS,
bounded GETs, no redirects, no caller URLs, no credentials or telemetry payloads.
"""
import ipaddress
import json
import re
import socket
from urllib.parse import urlencode, quote

from .research import PinnedHTTPS
from .utils import to_iso

PUBLIC_REPOSITORIES = {
    **{name: f"library/{name}" for name in ("fluentd", "nginx", "busybox", "alpine", "ubuntu", "debian", "python", "node", "redis", "postgres", "mysql", "mongo", "httpd", "rabbitmq", "memcached", "traefik", "golang", "haproxy")},
    "fluent/fluentd": "fluent/fluentd", "fluent/fluent-bit": "fluent/fluent-bit",
    "grafana/grafana": "grafana/grafana", "prom/prometheus": "prom/prometheus",
}
NOTICE = "Public registry metadata verifies published tags/platforms, not application compatibility, security, runtime health or human approval. Prefer pinned stable versions/digests and preserve application behavior. No images were pulled."


def normalize_reference(reference):
    reference = re.sub(r"^(?:docker\.io|index\.docker\.io)/", "", reference)
    return reference.removeprefix("library/")


def public_repository(reference):
    reference = normalize_reference(reference).split("@", 1)[0]
    repository = reference.rsplit(":", 1)[0] if ":" in reference else reference
    return repository if repository in PUBLIC_REPOSITORIES else None


def authorize(args, *, inspect=False):
    keys = {"repository", "tag"} if inspect else {"repository", "prefix", "architecture"}
    if not isinstance(args, dict) or set(args) - keys or args.get("repository") not in PUBLIC_REPOSITORIES:
        raise ValueError("Use a vetted public repository from the tool enum; private registries and arbitrary image URLs are not sent to the internet")
    if inspect and not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", str(args.get("tag", ""))):
        raise ValueError("Use one literal Docker image tag, not a URL, digest, path or query")
    if not inspect:
        if not re.fullmatch(r"(?:v?\d+(?:\.\d+){0,2}(?:[-_.](?:alpine|debian))?[-_.]?)?", str(args.get("prefix", ""))): raise ValueError("Use an empty prefix or a version prefix such as v1.19; arbitrary search text is not transmitted")
        if args.get("architecture", "amd64") not in {"amd64", "arm64"}: raise ValueError("Supported architecture filters are amd64 and arm64")
    return args


def _get(path):
    host = "hub.docker.com"
    addresses = sorted({row[4][0] for row in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)})
    if not addresses or not all(ipaddress.ip_address(a).is_global for a in addresses): raise ValueError("Registry DNS must resolve only to public IPs")
    connection = PinnedHTTPS(host, addresses[0])
    try:
        connection.request("GET", path, headers={"User-Agent": "Kravel-Public-Image-Research/0.5", "Accept": "application/json"})
        response = connection.getresponse()
        if response.status != 200: return response.status, {}
        if "application/json" not in response.getheader("Content-Type", "").lower(): raise ValueError("Registry returned non-JSON content; redirects/login pages are not followed")
        raw = response.read(1048577)
        if len(raw) > 1048576: raise ValueError("Registry metadata exceeds the download limit")
        return 200, json.loads(raw)
    finally:
        connection.close()


def _tag(row, repository):
    tag = row.get("name", "")
    if not isinstance(tag, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", tag): return None
    if row.get("tag_status", "active") != "active" or row.get("v2") is False: return None
    platforms = []
    for item in row.get("images", [])[:16]:
        if item.get("os") not in {"linux", "windows"} or item.get("architecture") not in {"amd64", "arm64", "arm", "386", "ppc64le", "s390x"}: continue
        digest = item.get("digest", "")
        platforms.append({"os": item["os"], "architecture": item["architecture"], "digest": digest if re.fullmatch(r"sha256:[0-9a-f]{64}", digest) else ""})
    digest = row.get("digest", "")
    ref = "docker.io/" + PUBLIC_REPOSITORIES[repository]
    return {"tag": tag, "reference": ref + ":" + tag, "digestReference": ref + "@" + digest if isinstance(digest, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", digest) else "", "platforms": platforms, "lastUpdated": str(row.get("last_updated", ""))[:40], "verified": True}


def _unavailable(status):
    return {"status": "not_found" if status == 404 else "unavailable", "httpStatus": status, "reason": "Public repository/tag was not found" if status == 404 else "Registry lookup unavailable or rate-limited; do not invent a successful verification"}


def search_image_tags(repository, prefix="", architecture="amd64"):
    authorize({"repository": repository, "prefix": prefix, "architecture": architecture})
    path = "/v2/namespaces/" + PUBLIC_REPOSITORIES[repository].replace("/", "/repositories/", 1) + "/tags?" + urlencode({"page_size": 100, **({"name": prefix} if prefix else {})})
    status, data = _get(path)
    result = {"repository": repository, "architecture": architecture, "source": "https://hub.docker.com" + path, "observedAt": to_iso(), "notice": NOTICE}
    if status != 200: return {**result, **_unavailable(status), "candidates": []}
    candidates = []
    for row in data.get("results", [])[:100]:
        tag = _tag(row, repository)
        if not tag or not re.match(r"^v?\d+\.\d+\.\d+(?:$|[-_.])", tag["tag"]) or re.search(r"(?:^|[-_.])(?:rc\d*|alpha\d*|beta\d*|dev|edge|nightly)(?:$|[-_.])", tag["tag"], re.I): continue
        if prefix and not tag["tag"].startswith(prefix): continue
        if not any(p["os"] == "linux" and p["architecture"] == architecture for p in tag["platforms"]): continue
        candidates.append(tag)
    candidates.sort(key=lambda t: (tuple(int(n) for n in re.findall(r"\d+", t["tag"])[:3]), t["lastUpdated"]), reverse=True)
    return {**result, "status": "verified" if candidates else "no_matching_candidates", "candidates": candidates[:8], "truncated": bool(data.get("next")) or len(candidates) > 8, "selection": "Bounded published stable-looking version tags with matching Linux architecture. This is not an exhaustive release/support recommendation."}


def inspect_image_tag(repository, tag):
    authorize({"repository": repository, "tag": tag}, inspect=True)
    path = "/v2/namespaces/" + PUBLIC_REPOSITORIES[repository].replace("/", "/repositories/", 1) + "/tags/" + quote(tag, safe="")
    status, data = _get(path)
    result = {"repository": repository, "source": "https://hub.docker.com" + path, "observedAt": to_iso(), "notice": NOTICE}
    if status != 200: return {**result, **_unavailable(status)}
    candidate = _tag(data, repository)
    return {**result, "status": "verified" if candidate else "unavailable", "candidate": candidate}
