from __future__ import annotations


def resource_key(kind: str, name: str, namespace: str) -> str:
    return f"{namespace}/{kind}/{name}"


def config_references(spec: dict) -> set[str]:
    names = set()
    for volume in spec.get("volumes", []):
        if volume.get("configMap", {}).get("name"):
            names.add(volume["configMap"]["name"])
        for source in volume.get("projected", {}).get("sources", []):
            if source.get("configMap", {}).get("name"):
                names.add(source["configMap"]["name"])
    for container in [*spec.get("containers", []), *spec.get("initContainers", [])]:
        for env in container.get("env", []):
            if env.get("valueFrom", {}).get("configMapKeyRef", {}).get("name"):
                names.add(env["valueFrom"]["configMapKeyRef"]["name"])
        for source in container.get("envFrom", []):
            if source.get("configMapRef", {}).get("name"):
                names.add(source["configMapRef"]["name"])
    return names


def build_connections(objects: list[dict], namespace: str, visible_ids: set[str]) -> list[dict]:
    """Observed reference relationships only; selectors do not imply live traffic."""
    indexed = {(obj["kind"], obj.get("metadata", {}).get("name", "")): obj for obj in objects}
    edges = {}

    def connect(source_kind, source_name, target_kind, target_name, relation, evidence):
        source = resource_key(source_kind, source_name, namespace)
        target = resource_key(target_kind, target_name, namespace)
        if source in visible_ids and target in visible_ids and source != target:
            edge_id = f"{source}>{target}:{relation}"
            edges[edge_id] = {"id": edge_id, "source": source, "target": target, "relation": relation, "evidence": evidence}

    for obj in objects:
        kind, metadata = obj["kind"], obj.get("metadata", {})
        name = metadata.get("name", "")
        for owner in metadata.get("ownerReferences", []):
            parent = indexed.get((owner.get("kind"), owner.get("name")))
            if parent and owner.get("controller") is True and (not owner.get("uid") or owner["uid"] == parent.get("metadata", {}).get("uid")):
                connect(owner["kind"], owner["name"], kind, name, "owns", "metadata.ownerReferences (controller)")
        if kind in {"Deployment", "ReplicaSet", "Pod"}:
            spec = obj.get("spec", {}) if kind == "Pod" else obj.get("spec", {}).get("template", {}).get("spec", {})
            for config_name in config_references(spec):
                connect("ConfigMap", config_name, kind, name, "configures", "Pod volume / env / envFrom ConfigMap reference")
        if kind == "Service":
            selector = obj.get("spec", {}).get("selector", {})
            if not selector:
                continue
            for (pod_kind, pod_name), pod in indexed.items():
                labels = pod.get("metadata", {}).get("labels", {})
                if pod_kind == "Pod" and all(labels.get(key) == value for key, value in selector.items()):
                    connect("Service", name, "Pod", pod_name, "selects", "Service spec.selector matches Pod labels; not a traffic measurement")
    return sorted(edges.values(), key=lambda edge: edge["id"])
