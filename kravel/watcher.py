from __future__ import annotations

import http.client
import json
import logging
import ssl
import threading
import time
from urllib.parse import quote


class KubernetesWatcher:
    def __init__(self, *, kube, resources, cluster_id, store):
        self.kube, self.resources, self.cluster_id, self.store = kube, resources, cluster_id, store
        self.stop_event = threading.Event()
        self.threads = []
        self.types = {}
        self.token = ""
        self.context = None

    def initialize(self):
        if not self.kube.host:
            raise RuntimeError("KUBERNETES_SERVICE_HOST is not set; the watcher runs in-cluster")
        with open(self.kube.token_path, encoding="utf-8") as handle:
            self.token = handle.read().strip()
        self.context = ssl.create_default_context(cafile=self.kube.ca_path)

    def _connection(self):
        return http.client.HTTPSConnection(self.kube.host, self.kube.port, context=self.context, timeout=330)

    def _request_json(self, path):
        connection = self._connection()
        try:
            connection.request("GET", path, headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"})
            response = connection.getresponse()
            body = response.read()
            if response.status >= 400:
                error = RuntimeError(f"Kubernetes API {path} returned {response.status}: {body[:300].decode(errors='replace')}")
                error.status_code = response.status
                raise error
            return json.loads(body or b"{}")
        finally:
            connection.close()

    def _typed(self, path, obj):
        if not obj or obj.get("kind") == "Status":
            return obj
        resource_type = self.types[path]
        return {**obj, "apiVersion": obj.get("apiVersion") or resource_type[0], "kind": obj.get("kind") or resource_type[1]}

    def _ingest(self, action, obj):
        if not obj or obj.get("kind") == "Status":
            return
        if obj.get("kind") == "Event":
            self.store.record_kubernetes_event(self.cluster_id, obj)
        else:
            self.store.record_resource_change(cluster_id=self.cluster_id, source="watch", action=action, object=obj)

    def _list(self, path):
        payload = self._request_json(path)
        list_kind = str(payload.get("kind", ""))
        if not list_kind.endswith("List") or not payload.get("apiVersion"):
            raise RuntimeError(f"Kubernetes list {path} omitted apiVersion or kind")
        self.types[path] = (payload["apiVersion"], list_kind[:-4])
        for obj in payload.get("items", []):
            self._ingest("ADDED", self._typed(path, obj))
        return payload.get("metadata", {}).get("resourceVersion", "")

    def _watch(self, path, resource_version):
        separator = "&" if "?" in path else "?"
        watch_path = f"{path}{separator}watch=1&allowWatchBookmarks=true&timeoutSeconds=300&resourceVersion={quote(resource_version)}"
        connection = self._connection()
        connection.request("GET", watch_path, headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"})
        response = connection.getresponse()
        if response.status >= 400:
            body = response.read(300)
            connection.close()
            error = RuntimeError(f"Kubernetes watch returned {response.status}: {body.decode(errors='replace')}")
            error.status_code = response.status
            raise error
        try:
            while not self.stop_event.is_set():
                line = response.readline()
                if not line:
                    break
                event = json.loads(line)
                if event.get("type") == "ERROR":
                    error = RuntimeError(event.get("object", {}).get("message", "Kubernetes watch error"))
                    error.status_code = event.get("object", {}).get("code")
                    raise error
                obj = event.get("object") or {}
                resource_version = obj.get("metadata", {}).get("resourceVersion", resource_version)
                if event.get("type") != "BOOKMARK":
                    self._ingest(event.get("type", "MODIFIED"), self._typed(path, obj))
            return resource_version
        finally:
            connection.close()

    def _run_resource(self, path):
        resource_version, failures = "", 0
        while not self.stop_event.is_set():
            try:
                if not resource_version:
                    resource_version = self._list(path)
                    logging.info("listed %s at resourceVersion=%s", path, resource_version)
                resource_version = self._watch(path, resource_version)
                failures = 0
            except Exception as exc:
                if self.stop_event.is_set():
                    break
                if getattr(exc, "status_code", None) == 410:
                    resource_version = ""
                failures += 1
                logging.warning("watch %s failed: %s", path, exc)
                self.stop_event.wait(min(2**failures, 30))

    def start(self):
        self.initialize()
        for path in self.resources:
            thread = threading.Thread(target=self._run_resource, args=(path,), name=f"watch-{path.rsplit('/', 1)[-1]}", daemon=True)
            thread.start()
            self.threads.append(thread)

    def stop(self):
        self.stop_event.set()
        for thread in self.threads:
            thread.join(timeout=0.1)
