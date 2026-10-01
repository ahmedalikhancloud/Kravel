"""Fixed, human-only fault injection; no free-form commands or agent access."""
from __future__ import annotations

import copy
import secrets
import threading
import time
import urllib.request
import json

from .guardrails import public_evidence
from .operator import NAMESPACE, HEALTHY_ARGS, CRASH_ARGS, OOM_ARGS
from .utils import to_iso

CATALOG = [
    {"id": "oom", "title": "Memory limit", "failure": "OOMKilled", "resource": "Deployment/oom-demo", "description": "Allocate 96 MiB in a container limited to 32 MiB."},
    {"id": "imagepull", "title": "Missing image", "failure": "ImagePullBackOff", "resource": "Deployment/image-demo", "description": "Use an intentionally nonexistent BusyBox image tag."},
    {"id": "crashloop", "title": "Startup failure", "failure": "CrashLoopBackOff", "resource": "Deployment/crash-demo", "description": "Exit the demo application with a simulated dependency error."},
    {"id": "configmap", "title": "Wrong setting", "failure": "Bad ConfigMap", "resource": "ConfigMap/config-demo", "description": "Set MODE=broken and restart its demo Deployment."},
    {"id": "network", "title": "Disconnected Service", "failure": "Selector mismatch", "resource": "Service/demo-gateway", "description": "Point the Service selector at a label with no matching Pods."},
]


def operations(scenario, action, stamp):
    if action not in {"break", "reset"} or scenario not in {s["id"] for s in CATALOG} | {"all"}:
        raise ValueError("Unknown fixed lab action or scenario")
    if action == "break" and scenario == "all":
        raise ValueError("Break one lesson at a time; reset all is supported")
    if scenario == "all":
        return [op for lab in CATALOG for op in operations(lab["id"], action, stamp)]
    broken = action == "break"
    def deployment(name, container):
        return {"kind": "deployments", "name": name, "patch": {"spec": {"replicas": 1, "template": {"metadata": {"annotations": {"kravel.dev/lab-action": stamp}}, "spec": {"containers": [{"name": name, **container}]}}}}}
    if scenario == "oom":
        return [deployment("oom-demo", {"command": ["awk"] if broken else ["sh", "-c"], "args": OOM_ARGS if broken else HEALTHY_ARGS, "resources": {"limits": {"memory": "32Mi"}}})]
    if scenario == "imagepull":
        return [deployment("image-demo", {"image": "busybox:kravel-demo-image-does-not-exist" if broken else "busybox:1.36", "imagePullPolicy": "Always" if broken else "IfNotPresent"})]
    if scenario == "crashloop":
        return [deployment("crash-demo", {"command": ["sh", "-c"], "args": CRASH_ARGS if broken else HEALTHY_ARGS})]
    if scenario == "configmap":
        return [{"kind": "configmaps", "name": "config-demo", "patch": {"data": {"MODE": "broken" if broken else "healthy"}}}, {"kind": "deployments", "name": "config-demo", "patch": {"spec": {"replicas": 1, "template": {"metadata": {"annotations": {"kravel.dev/lab-action": stamp, "kravel.dev/approved-config-restart": None}}}}}}]
    return [{"kind": "services", "name": "demo-gateway", "patch": {"spec": {"selector": {"app": "no-such-demo-app" if broken else "net-demo"}}}}]


class LabController:
    def __init__(self, console, idle_check=None):
        self.console, self.kube, self.store = console, console.kube, console.store
        self.previews = {}
        self.lock = threading.Lock()
        self.idle_check = idle_check or self._idle_check

    @staticmethod
    def _idle_check():
        # Fail closed if the independent debugger/broker cannot report activity.
        base = "http://kravel.kravel-system.svc.cluster.local:8080"
        with urllib.request.urlopen(base + "/v1/activity", timeout=5) as response:
            activity = json.load(response)
        if set(activity) != {"investigationActive", "verificationActive"} or any(type(v) is not bool for v in activity.values()):
            raise ValueError("Invalid activity check; refusing lab change")
        if any(activity.values()):
            raise ValueError("Wait for the active investigation/recovery check before changing a lab")
        for path, key in (("/v1/investigations", "runs"), ("/v1/proposals", "proposals")):
            with urllib.request.urlopen(base + path, timeout=5) as response:
                items = json.load(response)[key]
            if any(item.get("status") in {"running", "pending", "approved", "executing"} or (item.get("verification") or {}).get("status") == "running" for item in items):
                raise ValueError("Finish or reject the active investigation/approval before changing a lab")

    def preview(self, scenario, action, session):
        if not self.console.execution_lock.acquire(blocking=False):
            raise ValueError("A demo control action is already running")
        try:
            self.idle_check()
            ops = operations(scenario, action, f"{to_iso()}-{secrets.token_hex(4)}")
            targets, dry_runs = [], []
            for op in ops:
                obj = self.kube.get_resource(op["kind"], op["name"], NAMESPACE)["object"]
                metadata = obj.get("metadata", {})
                if not metadata.get("uid") or not metadata.get("resourceVersion"):
                    raise ValueError("Lab identity/version missing; refusing action")
                patch = copy.deepcopy(op["patch"])
                patch["metadata"] = {"uid": metadata["uid"], "resourceVersion": metadata["resourceVersion"]}
                mime = "application/strategic-merge-patch+json" if op["kind"] == "deployments" else "application/merge-patch+json"
                result = self.kube.patch(op["kind"], op["name"], NAMESPACE, patch, content_type=mime, dry_run=True)
                targets.append({**op, "uid": metadata["uid"], "fingerprint": self.console.fingerprint(obj), "contentType": mime})
                dry_runs.append({"resource": f"{op['kind']}/{op['name']}", "patch": op["patch"], "serverDryRun": self.console.bounded_output(result)})
            nonce = secrets.token_urlsafe(32)
            with self.lock:
                self.previews = {k: v for k, v in self.previews.items() if v["expires"] > time.monotonic()}
                if len(self.previews) >= 20:
                    raise ValueError("Too many previews; wait one minute")
                self.previews[nonce] = {"session": session, "expires": time.monotonic()+60, "operations": targets, "scenario": scenario, "action": action}
            self.store.record("operator", "lab.preview", actor="manual-human", details={"scenario": scenario, "action": action, "namespace": NAMESPACE})
            return {"previewId": nonce, "expiresInSeconds": 60, "scenario": scenario, "action": action, "namespace": NAMESPACE, "dryRuns": dry_runs, "note": "Only an explicit human confirmation applies this fixed lab action. Karl has no access."}
        finally:
            self.console.execution_lock.release()

    def confirm(self, nonce, session):
        if not self.console.execution_lock.acquire(blocking=False):
            raise ValueError("A demo control action is already running")
        try:
            with self.lock:
                item = self.previews.get(nonce)
                if not item or item["session"] != session:
                    raise ValueError("Preview unavailable for this human session")
                self.previews.pop(nonce)
            if item["expires"] <= time.monotonic():
                raise ValueError("Preview expired; review a fresh preview")
            self.idle_check()
            # Check every object before any write, then recheck each at apply time.
            for op in item["operations"]:
                self._revalidate(op)
            accepted = []
            try:
                for op in item["operations"]:
                    obj = self._revalidate(op)
                    patch = copy.deepcopy(op["patch"])
                    patch["metadata"] = {"uid": op["uid"], "resourceVersion": obj["metadata"]["resourceVersion"]}
                    result = self.kube.patch(op["kind"], op["name"], NAMESPACE, patch, content_type=op["contentType"], dry_run=False)
                    accepted.append({"resource": f"{op['kind']}/{op['name']}", "status": "accepted", "generation": result.get("object", {}).get("metadata", {}).get("generation")})
                    self.store.record("operator", "lab.patch_accepted", actor="manual-human", resource=accepted[-1]["resource"], details={"scenario": item["scenario"], "action": item["action"], "patch": public_evidence(op["patch"])})
            except Exception as exc:
                self.store.record("operator", "lab.partial_failure", actor="manual-human", outcome="failed", details={"accepted": accepted, "errorType": type(exc).__name__})
                return {"status": "partial_failure" if accepted else "failed", "accepted": accepted, "error": "The lab action stopped. Some changes may already be accepted; inspect the cluster, then review Reset. No automatic rollback.", "scenario": item["scenario"], "action": item["action"]}
            return {"status": "accepted", "accepted": accepted, "scenario": item["scenario"], "action": item["action"], "note": "Kubernetes accepted the patches. Watch the live cluster for the actual failure or recovery; this is not a readiness guarantee."}
        finally:
            self.console.execution_lock.release()

    def _revalidate(self, op):
        obj = self.kube.get_resource(op["kind"], op["name"], NAMESPACE)["object"]
        if obj.get("metadata", {}).get("uid") != op["uid"] or self.console.fingerprint(obj) != op["fingerprint"]:
            raise ValueError("Lab changed since preview; review a new preview")
        return obj
