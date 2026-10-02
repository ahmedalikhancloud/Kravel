"""Conservative symptom matching from live API evidence, never RAG scores."""
from __future__ import annotations

import re

from .remediation import matching_profiles
from .scenarios import by_id

EVENT_PATTERNS = [
    ("legacy_image_format", r"prettyjws|schema.?1|media type.+no longer supported"),
    ("image_not_found", r"manifest unknown|not found|invalid image name"),
    ("registry_auth", r"unauthorized|authentication required|pull access denied"),
    ("registry_connectivity", r"x509|no such host|i/o timeout|dial tcp|tls handshake timeout"),
    ("missing_configmap", r"configmap.+not found|couldn't find key.+configmap"),
    ("missing_secret", r"secret.+not found|couldn't find key.+secret"),
    ("liveness_failure", r"liveness probe failed"),
    ("readiness_failure", r"readiness probe failed"),
    ("startup_failure", r"startup probe failed"),
    ("cpu_request", r"insufficient cpu"),
    ("memory_request", r"insufficient memory"),
    ("node_selector", r"didn't match.+(?:affinity|selector)"),
    ("taint_untolerated", r"untolerated taint"),
    ("failed_mount", r"failedmount|mountvolume.+failed"),
    ("csi_multi_attach", r"multi-attach"),
    ("volume_node_affinity", r"volume node affinity conflict"),
    ("topology_spread", r"topology spread"),
    ("ip_exhaustion", r"no available ip|ipam.+exhaust|failed to allocate.+ip"),
    ("ephemeral_storage_eviction", r"ephemeral-storage|diskpressure|inodes"),
]


def ready_pod(pod):
    status = pod.get("status", {})
    return status.get("phase") == "Running" and bool(status.get("containerStatuses")) and all(s.get("ready") and "running" in s.get("state", {}) for s in status["containerStatuses"])


def event_matches(pod, events):
    meta = pod.get("metadata", {})
    matches = set()
    if ready_pod(pod):
        return matches  # Old Events are not a current incident.
    for event in events:
        ref = event.get("involvedObject", {})
        if ref.get("name") != meta.get("name") or (ref.get("uid") and ref["uid"] != meta.get("uid")):
            continue
        text = str(event.get("reason", "")) + " " + str(event.get("message", ""))
        matches.update(sid for sid, pattern in EVENT_PATTERNS if (sid not in {"legacy_image_format", "image_not_found", "registry_auth", "registry_connectivity"} or "image" in text.lower() and event.get("reason") in {"Failed", "ErrImagePull", "BackOff"}) and re.search(pattern, text, re.I))
    return matches


def enrich_findings(findings, objects, events, namespace):
    pods = objects.get("pods", [])
    by_name = {p.get("metadata", {}).get("name"): p for p in pods}
    owners = {(kind, obj.get("metadata", {}).get("name")): obj for kind, collection in (("ReplicaSet", "replicasets"), ("Deployment", "deployments"), ("DaemonSet", "daemonsets"), ("StatefulSet", "statefulsets")) for obj in objects.get(collection, [])}
    for finding in findings:
        kind, name = finding["resource"].split("/", 1)
        pod = by_name.get(name) if kind == "Pod" else None
        ids = set(finding.get("scenarioIds", []))
        cause = finding.get("cause", finding.get("evidence", "")).lower()
        if pod:
            ids.update(event_matches(pod, events))
            if "oomkilled" in cause:
                ids.add("oomkilled")
            if "crashloop" in cause or "exit code" in cause:
                ids.add("crash_command")
            if "init" in cause:
                ids.add("init_failure")
            if "imagepull" in cause and not ids:
                finding["evidenceNeeded"] = "Read the pull Event to distinguish tag, credentials, connectivity, and image format."
            # Follow UID-validated controller ownership; labels never authorize a fix.
            obj = pod
            for _ in range(2):
                owner = next((o for o in obj.get("metadata", {}).get("ownerReferences", []) if o.get("controller") is True), None)
                parent = owners.get((owner.get("kind"), owner.get("name"))) if owner else None
                if not parent or not owner.get("uid") or owner["uid"] != parent.get("metadata", {}).get("uid"):
                    break
                obj = parent
                kind, name = owner["kind"], owner["name"]
        elif kind == "ConfigMap" and "mode=" in cause:
            ids.add("bad_configmap")
        elif kind == "Service":
            ids.add("service_selector" if "no observed pods" in cause or "selector mismatch" in cause else "no_ready_endpoints")
        finding["scenarioIds"] = sorted(ids)
        finding["controllerResource"] = f"{kind}/{name}" if pod and kind != "Pod" else ""
        if pod and finding.get("fixId"):
            expected = {"fix_oom_memory": "oom-demo", "fix_image_pull": "image-demo", "fix_crash_loop": "crash-demo"}.get(finding["fixId"])
            if expected and (kind != "Deployment" or name != expected):
                finding["fixId"] = ""  # Labels must never select another workload's repair.
        profiles = matching_profiles(kind, name, ids) if namespace == "kravel-demo" else []
        if profiles and not finding.get("fixId"):
            # Multiple capabilities must be explicitly selected; never auto-compose patches.
            finding["fixId"] = profiles[0]["id"] if len(profiles) == 1 else ""
            finding["availableProfileIds"] = [p["id"] for p in profiles]
        if finding.get("fixId"):
            finding["repairAvailability"] = "Human approval required for this exact allowlisted repair."
        elif any(by_id(sid).get("executionMode") == "operator_led" for sid in ids):
            finding["repairAvailability"] = "Operator-led repair: additional owner evidence/authority is required; Karl cannot execute this runbook."
        else:
            finding["repairAvailability"] = "No executable repair enrolled for this object. An operator must review a healthy baseline and grant named broker permissions first."
    return findings


def controlled_pods(controller, pods, replicasets=()):
    """Exact UID/controller ownership, not app labels or similar error types."""
    uid = controller.get("metadata", {}).get("uid")
    if not uid:
        return []
    parent_uids = {uid}
    if controller.get("kind", "Deployment") == "Deployment":
        parent_uids.update(rs.get("metadata", {}).get("uid") for rs in replicasets if any(owner.get("controller") is True and owner.get("uid") == uid for owner in rs.get("metadata", {}).get("ownerReferences", [])))
    parent_uids.discard(None)
    return [pod for pod in pods if any(owner.get("controller") is True and owner.get("uid") in parent_uids for owner in pod.get("metadata", {}).get("ownerReferences", []))]
