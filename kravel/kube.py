from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from .utils import sanitize_object


RESOURCE_MAP = {
    "pods": ("", "v1", "pods", True),
    "pod": ("", "v1", "pods", True),
    "configmaps": ("", "v1", "configmaps", True),
    "configmap": ("", "v1", "configmaps", True),
    "services": ("", "v1", "services", True),
    "service": ("", "v1", "services", True),
    "endpoints": ("", "v1", "endpoints", True),
    "events": ("", "v1", "events", True),
    "persistentvolumeclaims": ("", "v1", "persistentvolumeclaims", True),
    "pvc": ("", "v1", "persistentvolumeclaims", True),
    "serviceaccounts": ("", "v1", "serviceaccounts", True),
    "namespaces": ("", "v1", "namespaces", False),
    "namespace": ("", "v1", "namespaces", False),
    "nodes": ("", "v1", "nodes", False),
    "node": ("", "v1", "nodes", False),
    "deployments": ("apps", "v1", "deployments", True),
    "deployment": ("apps", "v1", "deployments", True),
    "replicasets": ("apps", "v1", "replicasets", True),
    "replicaset": ("apps", "v1", "replicasets", True),
    "statefulsets": ("apps", "v1", "statefulsets", True),
    "daemonsets": ("apps", "v1", "daemonsets", True),
    "jobs": ("batch", "v1", "jobs", True),
    "cronjobs": ("batch", "v1", "cronjobs", True),
    "ingresses": ("networking.k8s.io", "v1", "ingresses", True),
    "endpointslices": ("discovery.k8s.io", "v1", "endpointslices", True),
}


class KubernetesAPIError(RuntimeError):
    pass


class KubernetesClient:
    """Minimal in-cluster client. Its authority is entirely defined by its ServiceAccount."""

    def __init__(self, kube_config):
        if not kube_config.host:
            raise ValueError("Kubernetes in-cluster host is not configured")
        self.base = f"https://{kube_config.host}:{kube_config.port}"
        self.token_path = kube_config.token_path
        self.ssl_context = ssl.create_default_context(cafile=kube_config.ca_path)

    def _token(self) -> str:
        with open(self.token_path, encoding="utf-8") as stream:
            return stream.read().strip()

    def request(self, method: str, path: str, *, query: dict | None = None, body=None, content_type="application/json"):
        url = self.base + path
        encoded_query = urllib.parse.urlencode({key: value for key, value in (query or {}).items() if value not in (None, "")})
        if encoded_query:
            url += "?" + encoded_query
        data = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
        headers = {"Authorization": f"Bearer {self._token()}", "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = content_type
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers, method=method), context=self.ssl_context, timeout=20) as response:
                raw = response.read()
                payload = json.loads(raw) if raw and "json" in response.headers.get("content-type", "") else raw.decode(errors="replace")
                return payload, (time.perf_counter() - started) * 1000
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode(errors="replace")
            try:
                message = json.loads(raw).get("message", raw)
            except json.JSONDecodeError:
                message = raw
            raise KubernetesAPIError(f"Kubernetes API {exc.code}: {message}") from exc

    @staticmethod
    def resource_path(kind: str, namespace: str = "", name: str = "") -> str:
        definition = RESOURCE_MAP.get(str(kind).lower())
        if not definition:
            raise ValueError(f"Unsupported resource kind: {kind}")
        group, version, resource, namespaced = definition
        prefix = f"/apis/{group}/{version}" if group else f"/api/{version}"
        if namespaced:
            if not namespace:
                raise ValueError(f"namespace is required for {resource}")
            prefix += f"/namespaces/{urllib.parse.quote(namespace, safe='')}"
        path = f"{prefix}/{resource}"
        if name:
            path += "/" + urllib.parse.quote(name, safe="")
        return path

    def list_resources(self, kind: str, namespace: str, selector: str = "", field_selector: str = "", limit: int = 100):
        payload, elapsed = self.request("GET", self.resource_path(kind, namespace), query={"labelSelector": selector, "fieldSelector": field_selector, "limit": min(max(int(limit), 1), 200)})
        return {"kind": payload.get("kind"), "items": [sanitize_object(item) for item in payload.get("items", [])], "durationMs": elapsed}

    def get_resource(self, kind: str, name: str, namespace: str):
        payload, elapsed = self.request("GET", self.resource_path(kind, namespace, name))
        return {"object": sanitize_object(payload), "durationMs": elapsed}

    def pod_logs(self, pod: str, namespace: str, container: str = "", previous: bool = False, tail_lines: int = 120):
        path = self.resource_path("pods", namespace, pod) + "/log"
        payload, elapsed = self.request("GET", path, query={"container": container, "previous": str(bool(previous)).lower(), "tailLines": min(max(int(tail_lines), 1), 500), "timestamps": "true"})
        return {"pod": pod, "namespace": namespace, "previous": bool(previous), "logs": str(payload)[-40_000:], "durationMs": elapsed}

    def events(self, namespace: str, regarding_name: str = "", limit: int = 100):
        field = f"involvedObject.name={regarding_name}" if regarding_name else ""
        result = self.list_resources("events", namespace, field_selector=field, limit=limit)
        result["items"].sort(key=lambda item: item.get("lastTimestamp") or item.get("eventTime") or item.get("metadata", {}).get("creationTimestamp", ""), reverse=True)
        return result

    def describe(self, kind: str, name: str, namespace: str):
        resource = self.get_resource(kind, name, namespace)
        events = self.events(namespace, name, 50)
        obj = resource["object"]
        return {
            "resource": obj,
            "events": events["items"],
            "summary": {
                "apiVersion": obj.get("apiVersion"),
                "kind": obj.get("kind"),
                "namespace": obj.get("metadata", {}).get("namespace", ""),
                "name": obj.get("metadata", {}).get("name", ""),
                "labels": obj.get("metadata", {}).get("labels", {}),
                "phase": obj.get("status", {}).get("phase", ""),
                "conditions": obj.get("status", {}).get("conditions", []),
            },
            "durationMs": resource["durationMs"] + events["durationMs"],
        }

    def patch(self, kind: str, name: str, namespace: str, patch: dict, *, content_type: str, dry_run: bool):
        payload, elapsed = self.request(
            "PATCH",
            self.resource_path(kind, namespace, name),
            query={"dryRun": "All" if dry_run else ""},
            body=patch,
            content_type=content_type,
        )
        return {"object": sanitize_object(payload), "dryRun": dry_run, "durationMs": elapsed}
