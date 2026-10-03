from __future__ import annotations

import time
from datetime import datetime, timezone

from .kube import RESOURCE_MAP
from .topology import build_connections, resource_key
from .diagnostics import controlled_pods, enrich_findings


READ_ONLY_TOOLS = [
    {"type": "function", "function": {"name": "get_pods", "description": "List Pods and their current container states. Equivalent to a structured kubectl get pods.", "parameters": {"type": "object", "properties": {"namespace": {"type": "string"}, "label_selector": {"type": "string"}}, "required": ["namespace"]}}},
    {"type": "function", "function": {"name": "get_resource", "description": "Get one Kubernetes resource as sanitized JSON. Secrets are unsupported.", "parameters": {"type": "object", "properties": {"kind": {"type": "string"}, "name": {"type": "string"}, "namespace": {"type": "string"}}, "required": ["kind", "name", "namespace"]}}},
    {"type": "function", "function": {"name": "describe_resource", "description": "Describe a resource with status, conditions, and related Kubernetes Events.", "parameters": {"type": "object", "properties": {"kind": {"type": "string"}, "name": {"type": "string"}, "namespace": {"type": "string"}}, "required": ["kind", "name", "namespace"]}}},
    {"type": "function", "function": {"name": "pod_logs", "description": "Read bounded Pod logs; never execs. Inspect container state first. previous=true is useful only with lastState.terminated. An image-pull failure may have no logs because the container never started; use Events, not repeated log requests.", "parameters": {"type": "object", "properties": {"pod": {"type": "string"}, "namespace": {"type": "string"}, "container": {"type": "string"}, "previous": {"type": "boolean"}, "tail_lines": {"type": "integer", "minimum": 1, "maximum": 500}}, "required": ["pod", "namespace"]}}},
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
    statuses = [*(pod.get("status", {}).get("initContainerStatuses") or []), *(pod.get("status", {}).get("containerStatuses") or [])]
    for status in statuses:
        last = status.get("lastState", {}).get("terminated", {})
        current = status.get("state", {})
        waiting = current.get("waiting", {})
        terminated = current.get("terminated", {})
        # lastState and restartCount are history, not an active incident. A
        # currently Ready/running container must not inherit an old repair card
        # after Docker restarts or a successful recovery. Keep that history in
        # the resource detail/trace; a subsequent failure is observed on refresh.
        if status.get("ready") is True and "running" in current:
            continue
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
    pod_owners = {owner.get("name") for pod in pods for owner in pod.get("metadata", {}).get("ownerReferences", []) if owner.get("kind") == "ReplicaSet"}
    replicasets = [obj for obj in kube.list_resources("replicasets", namespace, limit=200)["items"] if int(obj.get("spec", {}).get("replicas", 0) or 0) > 0 or obj.get("metadata", {}).get("name") in pod_owners]
    configmaps = kube.list_resources("configmaps", namespace, limit=100)["items"]
    services = kube.list_resources("services", namespace, limit=100)["items"]
    daemonsets = kube.list_resources("daemonsets", namespace, limit=100)["items"]
    statefulsets = kube.list_resources("statefulsets", namespace, limit=100)["items"]
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
        owned_names = {p["metadata"]["name"] for p in controlled_pods({**deployment, "kind": "Deployment"}, pods, replicasets)}
        issue = next((item for item in issues if item["resource"] in {"Pod/" + name for name in owned_names}), None)
        if not issue and metadata.get("name") == "config-demo" and config_broken:
            issue = next((item for item in issues if item["resource"] == "ConfigMap/config-demo"), None)
        resources.append({
            "kind": "Deployment", "name": metadata.get("name", ""), "namespace": namespace,
            "status": issue["title"] if issue else f"{available}/{desired} available",
            "health": issue["severity"] if issue else ("healthy" if desired == available else "warning"),
            "ready": desired == available, "issueType": issue["type"] if issue else "",
        })
    for kind, controllers in (("DaemonSet", daemonsets), ("StatefulSet", statefulsets)):
        for controller in controllers:
            metadata, spec, status = controller.get("metadata", {}), controller.get("spec", {}), controller.get("status", {})
            desired = int(status.get("desiredNumberScheduled", 0) if kind == "DaemonSet" else spec.get("replicas", 1))
            available = int(status.get("numberReady", 0) if kind == "DaemonSet" else status.get("readyReplicas", 0))
            ready = desired > 0 and available == desired and status.get("observedGeneration", 0) >= metadata.get("generation", 0)
            resources.append({"kind": kind, "name": metadata.get("name", ""), "namespace": namespace, "status": f"{available}/{desired} ready", "health": "healthy" if ready else "warning", "ready": ready, "issueType": ""})
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
    for replicaset in replicasets:
        name = replicaset.get("metadata", {}).get("name", "")
        desired = int(replicaset.get("spec", {}).get("replicas", 0) or 0)
        ready = int(replicaset.get("status", {}).get("readyReplicas", 0) or 0)
        related_pods = [pod for pod in pods if any(owner.get("kind") == "ReplicaSet" and owner.get("name") == name for owner in pod.get("metadata", {}).get("ownerReferences", []))]
        issue = next((issue_by_pod[pod["metadata"]["name"]] for pod in related_pods if pod["metadata"]["name"] in issue_by_pod), None)
        resources.append({"kind": "ReplicaSet", "name": name, "namespace": namespace, "status": issue["title"] if issue else f"{ready}/{desired} ready", "health": issue["severity"] if issue else ("healthy" if ready == desired else "warning"), "ready": ready == desired, "issueType": issue["type"] if issue else ""})
    for service in services:
        name = service.get("metadata", {}).get("name", "")
        selector = service.get("spec", {}).get("selector", {})
        mismatched = bool(selector) and not any(all(p.get("metadata", {}).get("labels", {}).get(k) == v for k, v in selector.items()) for p in pods)
        if mismatched:
            issues.append({"id": f"service_selector:{name}", "type": "service_selector", "title": "Service selector mismatch", "severity": "warning", "resource": f"Service/{name}", "evidence": "Selector matches no observed Pods; connectivity is untested.", "fixId": "fix_service_selector" if namespace == "kravel-demo" and name == "demo-gateway" else ""})
        resources.append({
            "kind": "Service", "name": name, "namespace": namespace,
            "status": "No matching Pods" if mismatched else service.get("spec", {}).get("type", "ClusterIP"),
            "health": "warning" if mismatched else "healthy", "ready": not mismatched, "issueType": "service_selector" if mismatched else "",
        })
    for resource in resources:
        resource["id"] = resource_key(resource["kind"], resource["name"], namespace)
    objects = [*({**obj, "kind": "Pod"} for obj in pods), *({**obj, "kind": "Deployment"} for obj in deployments), *({**obj, "kind": "ReplicaSet"} for obj in replicasets), *({**obj, "kind": "DaemonSet"} for obj in daemonsets), *({**obj, "kind": "StatefulSet"} for obj in statefulsets), *({**obj, "kind": "ConfigMap"} for obj in configmaps), *({**obj, "kind": "Service"} for obj in services)]
    # Event enrichment is needed only when a current symptom exists. Failure is
    # explicit coverage loss, not permission to infer a cause from old history.
    gaps, events = [], []
    if issues:
        try:
            events = kube.events(namespace, limit=100)["items"]
        except Exception:
            gaps.append("Recent Events unavailable; detailed symptom matching is incomplete.")
        enrich_findings(issues, {"pods": pods, "replicasets": replicasets, "deployments": deployments, "daemonsets": daemonsets, "statefulsets": statefulsets}, events, namespace)
    connections = build_connections(objects, namespace, {resource["id"] for resource in resources})
    return {
        "namespace": namespace,
        "observedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "podCount": len(pods),
        "healthyPods": sum(1 for pod in pods if pod.get("status", {}).get("phase") == "Running" and bool(pod.get("status", {}).get("containerStatuses")) and all(item.get("ready") for item in pod.get("status", {}).get("containerStatuses", []))),
        "issues": issues,
        "resources": resources,
        "connections": connections,
        "gaps": gaps,
        "durationMs": (time.perf_counter() - started) * 1000,
    }
