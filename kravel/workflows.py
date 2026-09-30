from __future__ import annotations

import threading
import time
import uuid
import json
import urllib.request
from datetime import datetime, timezone

from .debugger import run_debugger
from .evidence import Progress
from .fixes import get_fix
from .guardrails import public_evidence
from .tracing import MlflowTracer
from .utils import safe_service_url


def contains_patch(actual, expected):
    """Check reviewed fields, including named strategic-merge container lists."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and contains_patch(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        if expected and all(isinstance(x, dict) and "name" in x for x in expected):
            return isinstance(actual, list) and all(any(contains_patch(a, e) for a in actual) for e in expected)
        return actual == expected
    return actual == expected


def recovery_observation(kube, proposal):
    namespace = proposal["namespace"]
    fix = get_fix(proposal["fix_id"], namespace, proposal["id"])
    applied = proposal.get("result", {}).get("operations", [])
    if len(applied) != len(fix["operations"]):
        return False, {"reason": "Not all reviewed operations have an acceptance result."}
    details = []
    for operation, accepted in zip(fix["operations"], applied):
        obj = kube.get_resource(operation["kind"], operation["name"], namespace)["object"]
        metadata = obj.get("metadata", {})
        if not accepted.get("uid") or metadata.get("uid") != accepted["uid"] or not contains_patch(obj, operation["patch"]):
            return False, {"reason": "Applied resource was replaced or reviewed fields changed."}
        if operation["kind"] == "deployments":
            status, spec = obj.get("status", {}), obj.get("spec", {})
            generation = accepted.get("generation")
            desired = spec.get("replicas", 1)
            rs = kube.list_resources("replicasets", namespace, limit=100)["items"]
            current_rs = [r for r in rs if any(o.get("uid") == metadata["uid"] and o.get("controller") is True for o in r.get("metadata", {}).get("ownerReferences", [])) and int(r.get("spec", {}).get("replicas", 0)) > 0 and contains_patch(r.get("spec", {}).get("template", {}), spec.get("template", {}))]
            rs_uids = {r.get("metadata", {}).get("uid") for r in current_rs}
            pods = kube.list_resources("pods", namespace, limit=100)["items"]
            current_pods = [p for p in pods if not p.get("metadata", {}).get("deletionTimestamp") and any(o.get("uid") in rs_uids and o.get("controller") is True for o in p.get("metadata", {}).get("ownerReferences", []))]
            ready = len(current_pods) >= desired > 0 and all(p.get("status", {}).get("phase") == "Running" and p.get("status", {}).get("containerStatuses") and all(c.get("ready") and "running" in c.get("state", {}) for c in p["status"]["containerStatuses"]) for p in current_pods)
            ready = ready and generation is not None and status.get("observedGeneration", 0) >= generation and all(status.get(field, 0) == desired for field in ("replicas", "updatedReplicas", "availableReplicas", "readyReplicas"))
            details.append({"resource": f"Deployment/{operation['name']}", "generation": generation, "observedGeneration": status.get("observedGeneration"), "currentPods": [p["metadata"]["name"] for p in current_pods], "ready": bool(ready)})
            if not ready:
                return False, {"checks": details, "reason": "Waiting for applied-generation rollout and owned current Pods to become Ready."}
        elif operation["kind"] == "services":
            slices = kube.list_resources("endpointslices", namespace, selector=f"kubernetes.io/service-name={operation['name']}", limit=100)["items"]
            pods = kube.list_resources("pods", namespace, selector="app=net-demo", limit=100)["items"]
            pod_uids = {p["metadata"]["uid"] for p in pods if not p["metadata"].get("deletionTimestamp") and p.get("status", {}).get("phase") == "Running" and bool(p.get("status", {}).get("containerStatuses")) and all(c.get("ready") for c in p["status"]["containerStatuses"])}
            ready = any((e.get("conditions") or {}).get("ready") is True and (e.get("targetRef") or {}).get("uid") in pod_uids for s in slices for e in (s.get("endpoints") or []))
            details.append({"resource": "Service/demo-gateway", "readyEndpoints": ready, "trafficProbe": "Not performed; Pod HTTP readiness is reported by Kubernetes."})
            if not ready:
                return False, {"checks": details, "reason": "Waiting for Ready endpoints to the observed HTTP workload."}
        else:
            details.append({"resource": f"ConfigMap/{operation['name']}", "reviewedFieldsMatch": True})
    return True, {"checks": details, "reason": "Reviewed fields and current workload readiness observed."}


class WorkflowManager:
    """One local-model run, two independent read-only verification observers."""
    def __init__(self, kube, store, config):
        self.kube, self.store, self.config = kube, store, config
        self.model_slot = threading.Lock()
        self.verifier_slots = threading.BoundedSemaphore(2)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        store.interrupt_workflows("investigation")
        store.interrupt_workflows("verification")

    def start_observer(self):
        def observe():
            while not self.stop.is_set():
                try:
                    with urllib.request.urlopen(safe_service_url(self.config.broker_url, "broker") + "/v1/proposals", timeout=10) as response:
                        self.observe_proposals(json.load(response).get("proposals", []))
                except Exception:
                    pass  # UI explicitly reports broker unavailability; no mutation/replay.
                self.stop.wait(5)
        threading.Thread(target=observe, daemon=True, name="read-only-recovery-observer").start()

    def start(self, question, namespace, target=""):
        if namespace != self.config.default_namespace or len(question) > 8000:
            raise ValueError("Investigation must use the configured namespace and a bounded question")
        if not self.model_slot.acquire(blocking=False):
            raise RuntimeError("Karl is already investigating. Follow the active run, then try again.")
        run_id = str(uuid.uuid4())
        self.store.start_workflow(run_id, "investigation", namespace, target)
        threading.Thread(target=self._investigate, args=(run_id, question, namespace, target), daemon=True).start()
        return self.store.workflow(run_id)

    def _investigate(self, run_id, question, namespace, target):
        try:
            result = run_debugger(self.kube, self.store, self.config, question, namespace, run_id=run_id, progress=Progress(self.store, run_id), target=target)
            # Raw guarded operator prompt is never part of public history.
            result.get("guardrails", {}).get("input", {}).pop("value", None)
            self.store.update_workflow(run_id, status="completed", payload=public_evidence(result))
        except Exception as exc:
            payload = self.store.workflow(run_id)["payload"]
            payload["error"] = public_evidence(str(exc))
            for step in self.store.workflow(run_id)["steps"]:
                if step["status"] == "running":
                    self.store.workflow_step(run_id, step["step_key"], step["label"], "failed", details={"error": payload["error"]})
            self.store.update_workflow(run_id, status="failed", payload=payload)
        finally:
            self.model_slot.release()

    def observe_proposals(self, proposals):
        for proposal in proposals:
            run_id = "verify:" + proposal["id"]
            with self.lock:
                if proposal["status"] == "executed" and not self.store.workflow(run_id):
                    # Avoid retroactively claiming recovery for old, unrelated repairs.
                    at = datetime.fromisoformat(proposal["executed_at"].replace("Z", "+00:00"))
                    if (datetime.now(timezone.utc)-at).total_seconds() < 600 and self.verifier_slots.acquire(blocking=False):
                        self.store.start_workflow(run_id, "verification", proposal["namespace"], proposal["resource"])
                        threading.Thread(target=self._verify, args=(run_id, proposal), daemon=True).start()
            proposal["verification"] = self.store.workflow(run_id)
        return proposals

    def _verify(self, run_id, proposal):
        started, stable = time.monotonic(), 0
        tracer = MlflowTracer(self.config.mlflow_url, self.config.mlflow_experiment)
        progress = Progress(self.store, run_id, tracer)
        last = {}
        try:
            with tracer.span("repair.verify", "CHAIN", {"proposal_id": proposal["id"]}), progress.step("recovery", "Observe rollout & stable recovery (90 seconds)"):
                while not self.stop.is_set() and time.monotonic()-started < 90:
                    with tracer.span("verification.observation", "TOOL"):
                        try:
                            ok, last = recovery_observation(self.kube, proposal)
                        except Exception as exc:
                            ok, last = False, {"reason": public_evidence(str(exc))}
                    stable = stable + 1 if ok else 0
                    self.store.update_workflow(run_id, payload={"observation": public_evidence(last), "stableObservations": stable, "traceId": tracer.trace_id})
                    if stable >= 3:
                        break
                    self.stop.wait(3)
            status = "recovered" if stable >= 3 else "interrupted" if self.stop.is_set() else "timeout"
            if status != "recovered":
                self.store.workflow_step(run_id, "recovery", "Observe rollout & stable recovery (90 seconds)", status, duration_ms=(time.monotonic()-started)*1000, details=last)
            self.store.update_workflow(run_id, status=status)
            self.store.record("debugger", "fix.verification", actor="read-only-verifier", resource=proposal["resource"], outcome=status, duration_ms=(time.monotonic()-started)*1000, trace_id=tracer.trace_id, details={"proposalId": proposal["id"], "stabilityWindowSeconds": 6, "observation": public_evidence(last)})
        except Exception as exc:
            self.store.update_workflow(run_id, status="failed", payload={"error": public_evidence(str(exc)), "observation": public_evidence(last)})
            self.store.record("debugger", "fix.verification", actor="read-only-verifier", resource=proposal["resource"], outcome="failed", details={"proposalId": proposal["id"], "errorType": type(exc).__name__})
        finally:
            tracer.flush()
            self.verifier_slots.release()
