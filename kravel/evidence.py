from __future__ import annotations

import time
from contextlib import contextmanager

from .guardrails import public_evidence
from .tools import _container_issue
from .topology import build_connections, resource_key
from .utils import to_iso
from .diagnostics import enrich_findings, event_matches


class Progress:
    def __init__(self, store, run_id, tracer=None):
        self.store, self.run_id, self.tracer = store, run_id, tracer

    @contextmanager
    def step(self, key, label, details=None):
        started = time.perf_counter()
        details = public_evidence(details or {})
        self.store.workflow_step(self.run_id, key, label, "running", details=details)
        try:
            yield details
        except Exception as exc:
            details["error"] = public_evidence(str(exc))
            self.store.workflow_step(self.run_id, key, label, "failed", duration_ms=(time.perf_counter()-started)*1000, details=details)
            raise
        else:
            self.store.workflow_step(self.run_id, key, label, "unavailable" if details.get("coverage") == "unavailable" else "completed", duration_ms=(time.perf_counter()-started)*1000, details=details)


def collect_evidence(kube, namespace, progress, tracer, target=""):
    """Bounded evidence pass. Failed reads are visible gaps, not invented facts."""
    evidence, objects, gaps, findings = [], {}, [], []

    def read(key, label, resource, call):
        with progress.step(key, label, {"resource": resource}) as details:
            try:
                with tracer.span(f"evidence.{key}", "TOOL", {"namespace": namespace}) as span:
                    span.set_content_inputs({"label": label, "resource": resource, "read_limit": 100 if not key.startswith("logs_") else 60})
                    result = call()
                    span.set_outputs({"status": "unavailable" if result.get("available") is False else "success"})
                    span.set_content_outputs({"result": result})
                body, outcome = public_evidence(result), "unavailable" if result.get("available") is False else "observed"
                if outcome == "unavailable": gaps.append(f"{label}: {body.get('reason', 'unavailable')}")
            except Exception as exc:
                body, outcome = {"error": public_evidence(str(exc))}, "unavailable"
                gaps.append(f"{label}: unavailable")
            eid = f"E{len(evidence)+1}"
            evidence.append({"id": eid, "resource": resource, "label": label, "status": outcome, "observedAt": to_iso(), "body": body})
            details.update({"evidenceId": eid, "coverage": outcome})
            progress.store.record("debugger", f"evidence.{key}", actor="collector", resource=resource, outcome=outcome, trace_id=tracer.trace_id, details={"runId": progress.run_id, "evidenceId": eid})
            progress.store.update_workflow(progress.run_id, payload={"evidence": evidence, "findings": findings, "gaps": gaps})
            return body, eid

    for kind, label in (("pods", "Container states & readiness"), ("deployments", "Rollout generations & conditions"), ("replicasets", "Ownership chain"), ("configmaps", "Referenced configuration"), ("services", "Service selectors"), ("endpointslices", "Ready network endpoints"), ("daemonsets", "Per-node workload ownership & rollout"), ("statefulsets", "Stateful workload revisions"), ("persistentvolumeclaims", "Storage binding & conditions"), ("jobs", "Task completion & retry conditions"), ("horizontalpodautoscalers", "Autoscaling conditions"), ("ingresses", "Ingress backend references"), ("networkpolicies", "Network policy selectors"), ("poddisruptionbudgets", "Disruption budget availability"), ("resourcequotas", "Namespace quota usage"), ("limitranges", "Namespace resource defaults")):
        result, eid = read(kind, label, "", lambda kind=kind: kube.list_resources(kind, namespace, limit=100))
        objects[kind] = result.get("items", [])
        objects[f"{kind}_eid"] = eid
        objects[f"{kind}_available"] = "items" in result
        objects[f"{kind}_truncated"] = bool(result.get("truncated"))
        if result.get("truncated"):
            gaps.append(f"{label}: result capped; additional objects were not collected")
    events, events_eid = read("events", "Recent Kubernetes Events", "", lambda: kube.events(namespace, limit=100))
    pods = [p for p in objects["pods"] if not p.get("metadata", {}).get("deletionTimestamp")]
    for pod in pods:
        name = pod.get("metadata", {}).get("name", "")
        resource = f"Pod/{name}"
        issue, fix, explanation = _container_issue(pod, namespace)
        if issue:
            findings.append({"resource": resource, "cause": explanation, "strength": "strong", "evidenceIds": [objects["pods_eid"]], "fixId": fix, "uncertainty": "Termination/waiting reason is observed; deeper application cause may require logs.", "prevention": {"oomkilled": "Profile memory and set a tested limit; a higher limit is not proof that a leak is fixed.", "imagepullbackoff": "Validate image tags and registry access before rollout.", "crashloopbackoff": "Validate startup arguments and configuration; add startup/readiness checks."}.get(issue, "Test changes before rollout.")})
        elif pod.get("status", {}).get("phase") == "Pending":
            related = [e for e in events.get("items", []) if e.get("involvedObject", {}).get("uid") == pod.get("metadata", {}).get("uid")]
            findings.append({"resource": resource, "cause": "Pod is Pending; inspect scheduling/mount Events.", "strength": "moderate", "evidenceIds": [objects["pods_eid"], events_eid], "fixId": "", "uncertainty": "Pending is a symptom, not a proven root cause.", "prevention": "Check resource capacity, placement constraints, and volume availability.", "events": related[:5]})
        elif not all(s.get("ready") for s in (pod.get("status", {}).get("containerStatuses") or [])):
            findings.append({"resource": resource, "cause": "Pod containers are not Ready; inspect probe and initialization evidence.", "strength": "moderate", "evidenceIds": [objects["pods_eid"], events_eid], "fixId": "", "uncertainty": "Readiness is a symptom, not a root cause.", "prevention": "Validate health checks and initialization dependencies."})

    target_id = f"{namespace}/{target}" if target else ""
    kinds = {"pods": "Pod", "deployments": "Deployment", "replicasets": "ReplicaSet", "configmaps": "ConfigMap", "services": "Service", "daemonsets": "DaemonSet", "statefulsets": "StatefulSet"}
    all_objects = [{**o, "kind": kinds[k]} for k in kinds for o in objects[k]]
    connected = {target_id} if target_id else set()
    links = build_connections(all_objects, namespace, {resource_key(o.get("kind", ""), o.get("metadata", {}).get("name", ""), namespace) for o in all_objects})
    for _ in range(3):
        connected.update(link["target"] for link in links if link["source"] in connected)
        connected.update(link["source"] for link in links if link["target"] in connected)
    candidates = [p for p in pods if (not target or resource_key("Pod", p["metadata"]["name"], namespace) in connected) and (_container_issue(p, namespace)[0] or not all(s.get("ready") for s in (p.get("status", {}).get("containerStatuses") or [])) or resource_key("Pod", p["metadata"]["name"], namespace) in connected)]
    log_count = 0
    for pod in candidates[:4]:
        name = pod["metadata"]["name"]
        for container in pod.get("spec", {}).get("containers", [])[:2]:
            if log_count >= 8:
                break
            cname = container.get("name", "")
            status = next((s for s in pod.get("status", {}).get("containerStatuses", []) if s.get("name") == cname), {})
            for previous in ([False, True] if status.get("lastState", {}).get("terminated") else [False]):
                if log_count >= 8:
                    break
                log_count += 1
                read(f"logs_{log_count}", f"{'Previous' if previous else 'Current'} logs · {cname}", f"Pod/{name}", lambda: kube.pod_logs(name, namespace, cname, previous, 60))
    for cm in objects["configmaps"]:
        if namespace == "kravel-demo" and cm.get("metadata", {}).get("name") == "config-demo" and cm.get("data", {}).get("MODE") != "healthy":
            findings.append({"resource": "ConfigMap/config-demo", "cause": f"MODE={cm.get('data', {}).get('MODE')!r}; this lab requires healthy.", "strength": "strong", "evidenceIds": [objects["configmaps_eid"]], "fixId": "fix_bad_configmap", "uncertainty": "ConfigMap data is observed. Consumers must reload or restart to pick up the repair.", "prevention": "Validate configuration against a schema and test startup with proposed values."})
    for svc in objects["services"]:
        selector = svc.get("spec", {}).get("selector", {})
        if not selector:
            continue
        if not objects["pods_available"]:
            continue  # A failed Pod list is not evidence of an empty selector match.
        name = svc["metadata"]["name"]
        matching = [p for p in pods if all(p.get("metadata", {}).get("labels", {}).get(k) == v for k, v in selector.items())]
        slices = [s for s in objects["endpointslices"] if s.get("metadata", {}).get("labels", {}).get("kubernetes.io/service-name") == name]
        ready = sum((e.get("conditions") or {}).get("ready") is True for s in slices for e in (s.get("endpoints") or []))
        if not matching or objects["endpointslices_available"] and not ready:
            findings.append({"resource": f"Service/{name}", "cause": "Selector matches no observed Pods." if not matching else "Matching Pods exist, but no explicitly Ready endpoints were observed.", "strength": "strong" if not matching and not objects["pods_truncated"] else "moderate", "evidenceIds": [objects["services_eid"], objects["pods_eid"], objects["endpointslices_eid"]], "fixId": "fix_service_selector" if namespace == "kravel-demo" and name == "demo-gateway" and not matching and not objects["pods_truncated"] else "", "uncertainty": "DNS, TCP, and HTTP connectivity are untested; endpoint configuration is not proof of traffic.", "prevention": "Test Service selectors and endpoint readiness during deployment."})
    for kind, resource_kind, predicate, scenario_id in (
        ("persistentvolumeclaims", "PersistentVolumeClaim", lambda o: o.get("status", {}).get("phase") == "Pending", "pvc_pending"),
        ("jobs", "Job", lambda o: any(c.get("type") == "Failed" and c.get("status") == "True" for c in o.get("status", {}).get("conditions", [])), "job_backoff"),
        ("horizontalpodautoscalers", "HorizontalPodAutoscaler", lambda o: any(c.get("type") == "ScalingActive" and c.get("status") == "False" for c in o.get("status", {}).get("conditions", [])), "hpa_missing_metrics"),
        ("deployments", "Deployment", lambda o: any(c.get("reason") == "ProgressDeadlineExceeded" and c.get("status") == "False" for c in o.get("status", {}).get("conditions", [])), "rollout_stalled"),
    ):
        for obj in objects[kind]:
            if predicate(obj):
                findings.append({"resource": resource_kind + "/" + obj["metadata"]["name"], "cause": f"Observed {scenario_id.replace('_', ' ')} status; underlying cause requires investigation.", "strength": "moderate", "evidenceIds": [objects[kind + "_eid"]], "scenarioIds": [scenario_id], "fixId": "", "uncertainty": "Status is observed, not proof of the underlying cause.", "prevention": "Follow the evidence requirements and verification in the associated runbook."})
    if target:
        findings = [f for f in findings if f"{namespace}/{f['resource']}" in connected or f"{namespace}/{f['resource']}" == target_id]
    enrich_findings(findings, objects, events.get("items", []), namespace)
    for finding in findings:
        pod = next((p for p in pods if finding["resource"] == "Pod/" + p["metadata"]["name"]), None)
        if pod and event_matches(pod, events.get("items", [])) and events_eid not in finding["evidenceIds"]:
            finding["evidenceIds"].append(events_eid)
    config_pods = {f"Pod/{p['metadata']['name']}" for p in pods if any(v.get("configMap", {}).get("name") == "config-demo" for v in p.get("spec", {}).get("volumes", []))}
    for finding in findings:
        finding["evidenceIds"].extend(e["id"] for e in evidence if "logs" in e["label"] and e["status"] == "observed" and (e["resource"] == finding["resource"] or finding["resource"] == "ConfigMap/config-demo" and e["resource"] in config_pods))
    bundle = {"evidence": evidence, "findings": findings, "gaps": gaps, "coverage": "Bounded namespace reads: up to 100 objects per kind, 4 affected/selected Pods, 8 log reads. No exec, secret reads, node shell, control-plane metrics, or traffic probes; some difficult cases require operator-supplied evidence.", "target": target}
    progress.store.update_workflow(progress.run_id, payload=bundle)
    return bundle
