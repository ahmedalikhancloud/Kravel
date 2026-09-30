from __future__ import annotations

from collections import deque

from .utils import object_identity


def _pod_spec(obj: dict):
    kind = obj.get("kind")
    if kind == "Pod":
        return obj.get("spec")
    if kind in {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"}:
        return obj.get("spec", {}).get("template", {}).get("spec")
    if kind == "CronJob":
        return obj.get("spec", {}).get("jobTemplate", {}).get("spec", {}).get("template", {}).get("spec")
    return None


def _pod_labels(obj: dict) -> dict:
    if obj.get("kind") == "Pod":
        return obj.get("metadata", {}).get("labels", {})
    return obj.get("spec", {}).get("template", {}).get("metadata", {}).get("labels", {})


def build_state_graph(objects: list[dict]) -> dict:
    identities: dict[str, dict] = {}
    by_uid: dict[str, str] = {}
    by_kind_name: dict[tuple[str, str, str], str] = {}
    for obj in objects:
        identity = {**object_identity(obj), "observed": True}
        identities[identity["key"]] = identity
        if identity["uid"]:
            by_uid[identity["uid"]] = identity["key"]
        by_kind_name[(identity["kind"], identity["namespace"], identity["name"])] = identity["key"]

    def resolve(kind: str, namespace: str, name: str, api_version: str = "v1"):
        if not name:
            return None
        existing = by_kind_name.get((kind, namespace, name))
        if existing:
            return existing
        key = f"{api_version}|{kind}|{namespace or '_cluster'}|{name}"
        identities[key] = {
            "apiVersion": api_version, "kind": kind, "namespace": namespace, "name": name,
            "uid": "", "resourceVersion": "", "key": key, "observed": False,
        }
        by_kind_name[(kind, namespace, name)] = key
        return key

    edges: list[dict] = []

    def add(source, target, relationship):
        edge = {"from": source, "to": target, "type": relationship}
        if source and target and source != target and edge not in edges:
            edges.append(edge)

    for obj in objects:
        source = object_identity(obj)
        metadata = obj.get("metadata", {})
        for owner in metadata.get("ownerReferences", []):
            target = by_uid.get(owner.get("uid")) or resolve(
                owner.get("kind", "Unknown"), source["namespace"], owner.get("name", ""), owner.get("apiVersion", "v1")
            )
            add(source["key"], target, "owned-by")
        spec = _pod_spec(obj)
        if spec:
            if spec.get("serviceAccountName"):
                add(source["key"], resolve("ServiceAccount", source["namespace"], spec["serviceAccountName"]), "uses-service-account")
            for volume in spec.get("volumes", []):
                if volume.get("configMap", {}).get("name"):
                    add(source["key"], resolve("ConfigMap", source["namespace"], volume["configMap"]["name"]), "mounts-config")
                if volume.get("secret", {}).get("secretName"):
                    add(source["key"], resolve("Secret", source["namespace"], volume["secret"]["secretName"]), "mounts-secret")
                if volume.get("persistentVolumeClaim", {}).get("claimName"):
                    add(source["key"], resolve("PersistentVolumeClaim", source["namespace"], volume["persistentVolumeClaim"]["claimName"]), "mounts-volume")
            for container in [*spec.get("initContainers", []), *spec.get("containers", [])]:
                for entry in container.get("envFrom", []):
                    if entry.get("configMapRef", {}).get("name"):
                        add(source["key"], resolve("ConfigMap", source["namespace"], entry["configMapRef"]["name"]), "reads-config")
                    if entry.get("secretRef", {}).get("name"):
                        add(source["key"], resolve("Secret", source["namespace"], entry["secretRef"]["name"]), "reads-secret")
        if obj.get("kind") == "Service":
            selector = obj.get("spec", {}).get("selector", {})
            for target_obj in objects:
                labels = _pod_labels(target_obj)
                if target_obj.get("kind") == "Pod" and object_identity(target_obj)["namespace"] == source["namespace"] and selector and all(labels.get(k) == v for k, v in selector.items()):
                    add(source["key"], object_identity(target_obj)["key"], "selects")
    return {"nodes": sorted(identities.values(), key=lambda item: item["key"]), "edges": edges}


def trace_graph(graph: dict, resource_key: str, max_depth: int = 2) -> dict:
    seen = {resource_key}
    frontier = deque([(resource_key, 0)])
    edges = []
    while frontier:
        current, depth = frontier.popleft()
        if depth >= max_depth:
            continue
        for edge in graph["edges"]:
            if edge["from"] != current and edge["to"] != current:
                continue
            if edge not in edges:
                edges.append(edge)
            other = edge["to"] if edge["from"] == current else edge["from"]
            if other not in seen:
                seen.add(other)
                frontier.append((other, depth + 1))
    return {"nodes": [node for node in graph["nodes"] if node["key"] in seen], "edges": edges}
