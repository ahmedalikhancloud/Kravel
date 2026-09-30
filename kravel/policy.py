from __future__ import annotations

import re
import time


SEVERE = {"service_selector_drift", "bad_image_rollout"}
RUNBOOKS = {
    "config_regression": {"title": "Configuration regression containment", "checks": ["Verify the changed ConfigMap keys against the last known-good state.", "Confirm the affected workloads consume the ConfigMap."], "proposedActions": ["Restore only confirmed regressed keys after approval.", "Restart the workload only after human approval."]},
    "service_selector_drift": {"title": "Service backend restoration", "checks": ["Compare the Service selector with ready Pod labels."], "proposedActions": ["Restore the last approved selector after approval."]},
    "bad_image_rollout": {"title": "Bad image rollout containment", "checks": ["Confirm the image reference changed immediately before pull failures."], "proposedActions": ["Roll back to the last approved digest after approval."]},
    "scheduling_constraint": {"title": "Scheduling constraint correction", "checks": ["Compare selectors, affinity, taints, and requests with nodes."], "proposedActions": ["Correct only the confirmed impossible constraint after approval."]},
}
SIGNALS = {
    "config_regression": (re.compile(r"ConfigMap|/data/|envFrom|configMap", re.I), re.compile(r"BackOff|CrashLoop|Failed|Unhealthy", re.I)),
    "service_selector_drift": (re.compile(r"Service|Endpoint|selector|subsets", re.I), re.compile(r"Endpoint|Unhealthy|Failed", re.I)),
    "bad_image_rollout": (re.compile(r"Deployment|containers|image", re.I), re.compile(r"ImagePull|ErrImage|Pulling|Failed", re.I)),
    "scheduling_constraint": (re.compile(r"Deployment|nodeSelector|affinity|resources", re.I), re.compile(r"FailedScheduling|Unschedulable", re.I)),
}


def decide_policy(laya, config):
    started = time.perf_counter()
    ranked = sorted(laya["diagnosis"].items(), key=lambda item: item[1], reverse=True)
    top, runner = ranked[0], ranked[1][1] if len(ranked) > 1 else 0
    positives = [name for name, value in ranked if value >= config.positive_threshold]
    margin = top[1] - runner
    severe = top[0] in SEVERE and top[1] >= config.positive_threshold
    confidence = laya.get("confidence")
    ambiguous = top[1] < config.high_confidence or margin < config.minimum_margin or len(positives) > 1 or (confidence is not None and confidence < config.high_confidence)
    reasons = []
    if severe: reasons.append("severe_incident_class")
    if top[1] < config.high_confidence: reasons.append("top_probability_below_threshold")
    if margin < config.minimum_margin: reasons.append("classification_margin_too_small")
    if len(positives) > 1: reasons.append("multiple_positive_classes")
    if confidence is not None and confidence < config.high_confidence: reasons.append("classifier_confidence_below_threshold")
    if not reasons: reasons.append("routine_high_confidence")
    return {"route": "qwen_investigation" if severe or ambiguous else "predefined_runbook", "topDiagnosis": top[0], "topProbability": top[1], "margin": margin, "positiveDiagnoses": positives, "severe": severe, "ambiguous": ambiguous, "reasons": reasons, "thresholds": {"highConfidence": config.high_confidence, "minimumMargin": config.minimum_margin, "positiveThreshold": config.positive_threshold}, "latencyMs": (time.perf_counter() - started) * 1000}


def run_predefined_automation(store, cluster_id, baseline_at, incident_at, namespace, diagnosis):
    started = time.perf_counter()
    change_pattern, event_pattern = SIGNALS[diagnosis]
    diff = store.diff_states(cluster_id=cluster_id, from_at=baseline_at, to_at=incident_at, namespace=namespace)
    context = store.context_shard(cluster_id=cluster_id, incident_at=incident_at, lookback=180, namespace=namespace, limit=100)
    changes = [{"resourceKey": item["resourceKey"], "changeType": item["changeType"], "changedPaths": item["changedPaths"][:12]} for item in diff["changes"] if change_pattern.search(f"{item['resourceKey']} {' '.join(item['changedPaths'])}")][:12]
    events = [{"eventAt": item["eventAt"], "reason": item["reason"], "resourceKey": f"{item['regardingKind']}|{item['namespace']}|{item['regardingName']}"} for item in context["kubernetesEvents"] if event_pattern.search(f"{item['reason']} {item['note']}")][-12:]
    return {"value": {"status": "completed", "executionMode": "read_only_diagnostics", "runbook": diagnosis, "checksExecuted": ["temporal_state_diff", "incident_event_correlation"], "matchedChanges": changes, "matchedEvents": events, "remediationExecuted": False}, "latencyMs": (time.perf_counter() - started) * 1000}


def create_human_review_proposal(policy, diagnosis, predefined=None, qwen_report="", qwen_guardrail=None):
    started = time.perf_counter()
    relevant = policy["positiveDiagnoses"] or [policy["topDiagnosis"]]
    runbooks = [{"diagnosis": name, **RUNBOOKS[name]} for name in relevant if name in RUNBOOKS]
    value = {"status": "awaiting_human_review", "executionMode": "dry_run", "diagnosticAutomationExecuted": bool(predefined), "remediationExecuted": False, "route": policy["route"], "leadingDiagnosis": policy["topDiagnosis"], "diagnosisSource": "laya_system1", "probabilities": {name: diagnosis[name] for name in relevant}, "policyReasons": policy["reasons"], "runbooks": runbooks, "runbookBasis": "Laya positive classes; the human reviewer must reconcile any Qwen disagreement", "predefinedAutomation": predefined, "qwenInvestigation": qwen_report or None, "qwenOutputGuardrail": {"decision": qwen_guardrail["decision"], "findings": qwen_guardrail["findings"]} if qwen_guardrail else None, "approvalRequirement": "A human must validate the evidence, blast radius, rollback target, and commands before any mutation."}
    return {"value": value, "latencyMs": (time.perf_counter() - started) * 1000}
