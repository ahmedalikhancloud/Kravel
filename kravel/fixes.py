from __future__ import annotations

import copy


DEMO_NAMESPACE = "kravel-demo"

FIX_CATALOG = {
    "fix_oom_memory": {
        "title": "Raise the OOM demo memory limit",
        "resource": "Deployment/oom-demo",
        "command": "kubectl -n kravel-demo set resources deployment/oom-demo --containers=oom-demo --limits=memory=128Mi",
        "operations": [
            {
                "kind": "deployments",
                "name": "oom-demo",
                "contentType": "application/strategic-merge-patch+json",
                "patch": {"spec": {"template": {"spec": {"containers": [{"name": "oom-demo", "resources": {"limits": {"memory": "128Mi"}}}]}}}},
            }
        ],
    },
    "fix_image_pull": {
        "title": "Restore the known-good image",
        "resource": "Deployment/image-demo",
        "command": "kubectl -n kravel-demo set image deployment/image-demo image-demo=busybox:1.36",
        "operations": [
            {
                "kind": "deployments",
                "name": "image-demo",
                "contentType": "application/strategic-merge-patch+json",
                "patch": {"spec": {"template": {"spec": {"containers": [{"name": "image-demo", "image": "busybox:1.36", "imagePullPolicy": "IfNotPresent"}]}}}},
            }
        ],
    },
    "fix_crash_loop": {
        "title": "Restore the healthy container command",
        "resource": "Deployment/crash-demo",
        "command": "kubectl -n kravel-demo patch deployment crash-demo --type=strategic -p '{\"spec\":{\"template\":{\"spec\":{\"containers\":[{\"name\":\"crash-demo\",\"command\":[\"sh\",\"-c\"],\"args\":[\"exec sleep 86400\"]}]}}}}'",
        "operations": [
            {
                "kind": "deployments",
                "name": "crash-demo",
                "contentType": "application/strategic-merge-patch+json",
                "patch": {"spec": {"template": {"spec": {"containers": [{"name": "crash-demo", "command": ["sh", "-c"], "args": ["exec sleep 86400"]}]}}}},
            }
        ],
    },
    "fix_bad_configmap": {
        "title": "Restore the ConfigMap's healthy mode",
        "resource": "ConfigMap/config-demo",
        "command": "kubectl -n kravel-demo patch configmap config-demo --type=merge -p '{\"data\":{\"MODE\":\"healthy\"}}' && kubectl -n kravel-demo rollout restart deployment/config-demo",
        "operations": [
            {
                "kind": "configmaps",
                "name": "config-demo",
                "contentType": "application/merge-patch+json",
                "patch": {"data": {"MODE": "healthy"}},
            },
            {
                "kind": "deployments",
                "name": "config-demo",
                "contentType": "application/strategic-merge-patch+json",
                "patch": {"spec": {"template": {"metadata": {"annotations": {"kravel.dev/approved-restart": "config-demo-v1"}}}}},
            },
        ],
    },
}


def get_fix(fix_id: str, namespace: str) -> dict:
    if namespace != DEMO_NAMESPACE:
        raise ValueError("The approval broker can mutate only the disposable kravel-demo namespace")
    if fix_id not in FIX_CATALOG:
        raise ValueError("Unknown or non-allowlisted fix")
    return copy.deepcopy({"id": fix_id, "namespace": namespace, **FIX_CATALOG[fix_id]})


def public_catalog() -> list[dict]:
    return [{"id": fix_id, "title": item["title"], "resource": item["resource"], "command": item["command"]} for fix_id, item in FIX_CATALOG.items()]


def summarize_result(operation: dict, response: dict) -> dict:
    obj = response.get("object", {})
    containers = obj.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    return {
        "resource": f"{obj.get('kind', operation['kind'])}/{obj.get('metadata', {}).get('name', operation['name'])}",
        "resourceVersion": obj.get("metadata", {}).get("resourceVersion", ""),
        "generation": obj.get("metadata", {}).get("generation"),
        "data": obj.get("data", {}),
        "containers": [
            {
                "name": container.get("name"),
                "image": container.get("image"),
                "command": container.get("command"),
                "args": container.get("args"),
                "resources": container.get("resources", {}),
            }
            for container in containers
        ],
        "dryRun": response.get("dryRun", False),
        "durationMs": response.get("durationMs", 0),
    }
