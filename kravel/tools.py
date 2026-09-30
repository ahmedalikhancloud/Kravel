from __future__ import annotations

import time
from datetime import datetime, timezone

from .kube import RESOURCE_MAP


READ_ONLY_TOOLS = [
    {"type": "function", "function": {"name": "get_pods", "description": "List Pods and their current container states. Equivalent to a structured kubectl get pods.", "parameters": {"type": "object", "properties": {"namespace": {"type": "string"}, "label_selector": {"type": "string"}}, "required": ["namespace"]}}},
    {"type": "function", "function": {"name": "get_resource", "description": "Get one Kubernetes resource as sanitized JSON. Secrets are unsupported.", "parameters": {"type": "object", "properties": {"kind": {"type": "string"}, "name": {"type": "string"}, "namespace": {"type": "string"}}, "required": ["kind", "name", "namespace"]}}},
    {"type": "function", "function": {"name": "describe_resource", "description": "Describe a resource with status, conditions, and related Kubernetes Events.", "parameters": {"type": "object", "properties": {"kind": {"type": "string"}, "name": {"type": "string"}, "namespace": {"type": "string"}}, "required": ["kind", "name", "namespace"]}}},
    {"type": "function", "function": {"name": "pod_logs", "description": "Read bounded current or previous Pod logs. This never execs into a container.", "parameters": {"type": "object", "properties": {"pod": {"type": "string"}, "namespace": {"type": "string"}, "container": {"type": "string"}, "previous": {"type": "boolean"}, "tail_lines": {"type": "integer", "minimum": 1, "maximum": 500}}, "required": ["pod", "namespace"]}}},
    {"type": "function", "function": {"name": "get_events", "description": "List recent Kubernetes Events, optionally scoped to one object name.", "parameters": {"type": "object", "properties": {"namespace": {"type": "string"}, "regarding_name": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}}, "required": ["namespace"]}}},
    {"type": "function", "function": {"name": "list_resources", "description": "List supported non-secret Kubernetes resources by kind and optional label selector.", "parameters": {"type": "object", "properties": {"kind": {"type": "string"}, "namespace": {"type": "string"}, "label_selector": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 200}}, "required": ["kind", "namespace"]}}},
]


def enforce_read_scope(name: str, args: dict, default_namespace: str) -> dict:
    scoped = dict(args or {})
    if name not in {item["function"]["name"] for item in READ_ONLY_TOOLS}:
        raise ValueError("Unknown read-only tool")
    scoped["namespace"] = str(scoped.get("namespace") or default_namespace)
    if scoped["namespace"] != default_namespace:
        raise ValueError("Tool namespace must match the selected investigation namespace")
    if scoped["namespace"] in {"*", "all", "all-namespaces"}:
        raise ValueError("Explicit namespace required; cross-namespace list calls are disabled")
    if name in {"get_resource", "describe_resource", "list_resources"}:
        kind = str(scoped.get("kind", "")).lower()
        if kind not in RESOURCE_MAP or "secret" in kind:
            raise ValueError("Unsupported or sensitive Kubernetes resource kind")
    if name == "pod_logs":
        scoped["tail_lines"] = min(max(int(scoped.get("tail_lines", 120)), 1), 500)
        scoped["previous"] = bool(scoped.get("previous", False))
    if name == "get_events":
        scoped["limit"] = min(max(int(scoped.get("limit", 100)), 1), 200)
    if name == "list_resources":
        scoped["limit"] = min(max(int(scoped.get("limit", 100)), 1), 200)
    return scoped


def execute_read_tool(name: str, args: dict, kube):
    if name == "get_pods":
        return kube.list_resources("pods", args["namespace"], selector=args.get("label_selector", ""), limit=100)
    if name == "get_resource":
        return kube.get_resource(args["kind"], args["name"], args["namespace"])
    if name == "describe_resource":
        return kube.describe(args["kind"], args["name"], args["namespace"])
    if name == "pod_logs":
        return kube.pod_logs(args["pod"], args["namespace"], args.get("container", ""), args.get("previous", False), args.get("tail_lines", 120))
    if name == "get_events":
        return kube.events(args["namespace"], args.get("regarding_name", ""), args.get("limit", 100))
    if name == "list_resources":
        return kube.list_resources(args["kind"], args["namespace"], selector=args.get("label_selector", ""), limit=args.get("limit", 100))
    raise ValueError("Unknown read-only tool")


def _container_issue(pod: dict, namespace: str = "kravel-demo"):
    app = pod.get("metadata", {}).get("labels", {}).get("app", "")
    is_demo = namespace == "kravel-demo"
    statuses = pod.get("status", {}).get("containerStatuses", [])
    for status in statuses:
        last = status.get("lastState", {}).get("terminated", {})
        current = status.get("state", {})
        waiting = current.get("waiting", {})
        terminated = current.get("terminated", {})
        if last.get("reason") == "OOMKilled" or terminated.get("reason") == "OOMKilled":
            return "oomkilled", "fix_oom_memory" if is_demo and app == "oom-demo" else "", f"container {status.get('name')} terminated with OOMKilled"
        reason = waiting.get("reason", "")
        if reason in {"ImagePullBackOff", "ErrImagePull", "InvalidImageName"}:
            return "imagepullbackoff", "fix_image_pull" if is_demo and app == "image-demo" else "", f"container {status.get('name')} is waiting: {reason}"
        prior_exit = int(last.get("exitCode", 0) or 0)
        if not (is_demo and app == "config-demo") and (reason == "CrashLoopBackOff" or prior_exit != 0 and int(status.get("restartCount", 0) or 0) > 0):
            evidence = "is waiting: CrashLoopBackOff" if reason == "CrashLoopBackOff" else f"restarted after exit code {prior_exit}"
            return "crashloopbackoff", "fix_crash_loop" if is_demo and app == "crash-demo" else "", f"container {status.get('name')} {evidence}"
    return "", "", ""


def discover_issues(kube, namespace: str) -> dict:
    started = time.perf_counter()
    pods = [pod for pod in kube.list_resources("pods", namespace, limit=200)["items"] if not pod.get("metadata", {}).get("deletionTimestamp")]
    deployments = kube.list_resources("deployments", namespace, limit=100)["items"]
    configmaps = kube.list_resources("configmaps", namespace, limit=100)["items"]
    services = kube.list_resources("services", namespace, limit=100)["items"]
    issues = []
    for pod in pods:
        issue_type, fix_id, evidence = _container_issue(pod, namespace)
        if not issue_type:
            continue
        name = pod.get("metadata", {}).get("name", "")
        issues.append({
            "id": f"{issue_type}:{name}",
            "type": issue_type,
            "title": {"oomkilled": "OOMKilled", "imagepullbackoff": "ImagePullBackOff", "crashloopbackoff": "CrashLoopBackOff"}[issue_type],
            "severity": "critical" if issue_type == "oomkilled" else "warning",
            "resource": f"Pod/{name}",
            "evidence": evidence,
            "fixId": fix_id,
        })
    config_broken = False
    try:
        config = kube.get_resource("configmaps", "config-demo", namespace)["object"]
        mode = config.get("data", {}).get("MODE", "")
        if mode != "healthy":
            config_broken = True
            issues.append({"id": "bad_configmap:config-demo", "type": "bad_configmap", "title": "Bad ConfigMap", "severity": "warning", "resource": "ConfigMap/config-demo", "evidence": f"data.MODE is {mode!r}; expected 'healthy'", "fixId": "fix_bad_configmap" if namespace == "kravel-demo" else ""})
    except Exception:
        pass

    issue_by_pod = {item["resource"].split("/", 1)[1]: item for item in issues if item["resource"].startswith("Pod/")}
    resources = []
    for pod in pods:
        metadata, status = pod.get("metadata", {}), pod.get("status", {})
        name = metadata.get("name", "")
        issue = issue_by_pod.get(name)
        app = metadata.get("labels", {}).get("app", "")
        if not issue and app == "config-demo" and config_broken:
            issue = next((item for item in issues if item["type"] == "bad_configmap"), None)
        ready = status.get("phase") == "Running" and bool(status.get("containerStatuses")) and all(item.get("ready") for item in status.get("containerStatuses", []))
        resources.append({
            "kind": "Pod", "name": name, "namespace": namespace,
            "status": issue["title"] if issue else ("Ready" if ready else status.get("phase", "Unknown")),
            "health": issue["severity"] if issue else ("healthy" if ready else "warning"),
            "ready": ready, "issueType": issue["type"] if issue else "",
        })
    for deployment in deployments:
        metadata, spec, status = deployment.get("metadata", {}), deployment.get("spec", {}), deployment.get("status", {})
        desired = int(spec.get("replicas", 1) or 0)
        available = int(status.get("availableReplicas", 0) or 0)
        app = metadata.get("labels", {}).get("app", metadata.get("name", ""))
        expected_issue = {"oom-demo": "oomkilled", "image-demo": "imagepullbackoff", "crash-demo": "crashloopbackoff", "config-demo": "bad_configmap"}.get(app, "")
        issue = next((item for item in issues if item["type"] == expected_issue), None)
        resources.append({
            "kind": "Deployment", "name": metadata.get("name", ""), "namespace": namespace,
            "status": issue["title"] if issue else f"{available}/{desired} available",
            "health": issue["severity"] if issue else ("healthy" if desired == available else "warning"),
            "ready": desired == available, "issueType": issue["type"] if issue else "",
        })
    for configmap in configmaps:
        name = configmap.get("metadata", {}).get("name", "")
        if name == "kube-root-ca.crt":
            continue
        broken = name == "config-demo" and config_broken
        resources.append({
            "kind": "ConfigMap", "name": name, "namespace": namespace,
            "status": "Invalid data" if broken else f"{len(configmap.get('data', {}))} data keys",
            "health": "warning" if broken else "healthy", "ready": not broken,
            "issueType": "bad_configmap" if broken else "",
        })
    for service in services:
        name = service.get("metadata", {}).get("name", "")
        resources.append({
            "kind": "Service", "name": name, "namespace": namespace,
            "status": service.get("spec", {}).get("type", "ClusterIP"),
            "health": "healthy", "ready": True, "issueType": "",
        })
    return {
        "namespace": namespace,
        "observedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "podCount": len(pods),
        "healthyPods": sum(1 for pod in pods if pod.get("status", {}).get("phase") == "Running" and all(item.get("ready") for item in pod.get("status", {}).get("containerStatuses", []))),
        "issues": issues,
        "resources": resources,
        "durationMs": (time.perf_counter() - started) * 1000,
    }
