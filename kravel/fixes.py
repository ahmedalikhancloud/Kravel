from __future__ import annotations

import copy
import shlex

from .utils import stable_json


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
    "fix_service_selector": {
        "title": "Reconnect the demo HTTP Service",
        "resource": "Service/demo-gateway",
        "operations": [{"kind": "services", "name": "demo-gateway", "contentType": "application/merge-patch+json", "patch": {"spec": {"selector": {"app": "net-demo"}}}}],
    },
}


def get_fix(fix_id: str, namespace: str, restart_marker: str = "") -> dict:
    if namespace != DEMO_NAMESPACE:
        raise ValueError("The approval broker can mutate only the disposable kravel-demo namespace")
    if fix_id not in FIX_CATALOG:
        raise ValueError("Unknown or non-allowlisted fix")
    fix = copy.deepcopy({"id": fix_id, "namespace": namespace, **FIX_CATALOG[fix_id]})
    if fix_id == "fix_bad_configmap" and restart_marker:
        fix["operations"][1]["patch"]["spec"]["template"]["metadata"]["annotations"]["kravel.dev/approved-restart"] = restart_marker
    # The review command describes exactly the structured patches that are executed.
    fix["command"] = " && ".join(
        f"kubectl -n {DEMO_NAMESPACE} patch {operation['kind']} {operation['name']} "
        f"--type={'strategic' if 'strategic' in operation['contentType'] else 'merge'} "
        f"-p {shlex.quote(stable_json(operation['patch']))}"
        for operation in fix["operations"]
    )
    return fix


def public_catalog() -> list[dict]:
    return [{key: fix[key] for key in ("id", "title", "resource", "command")} for fix in (get_fix(fix_id, DEMO_NAMESPACE) for fix_id in FIX_CATALOG)]


def summarize_result(operation: dict, response: dict) -> dict:
    obj = response.get("object", {})
    containers = obj.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
    return {
        "resource": f"{obj.get('kind', operation['kind'])}/{obj.get('metadata', {}).get('name', operation['name'])}",
        "resourceVersion": obj.get("metadata", {}).get("resourceVersion", ""),
        "generation": obj.get("metadata", {}).get("generation"),
        "uid": obj.get("metadata", {}).get("uid", ""),
        "selector": obj.get("spec", {}).get("selector", {}),
        "data": obj.get("data", {}),
        "templateAnnotations": obj.get("spec", {}).get("template", {}).get("metadata", {}).get("annotations", {}),
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
