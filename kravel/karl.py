from __future__ import annotations

import time
import uuid

from .pipeline import run_incident_pipeline
from .tracing import MlflowTracer, safe_pipeline_inputs
from .utils import object_identity, to_iso


def _resource_label(key: str) -> str:
    parts = str(key or "").split("|")
    return f"{parts[-3]}/{parts[-1]}" if len(parts) >= 4 else key


def grounded_reply(store, config, request: dict) -> dict:
    action = request.get("action", "message")
    namespace = request.get("namespace", "kravel-demo")
    at = to_iso(request.get("at"))
    baseline = to_iso(request.get("baselineAt") or at)
    resource_key = request.get("resourceKey", "")
    if action == "greet":
        stats = store.stats()
        return {
            "kind": "greeting",
            "message": f"Hey, I’m Karl. I’m holding {stats['changes']} observed changes in temporal memory. Pick a resource or drag the clock and I’ll keep the evidence in sync.",
            "options": ["Show warning events", "Explain selected resource", "Prepare deep investigation"],
        }
    if action == "explain_resource" and resource_key:
        snapshot = store.state_at(cluster_id=config.cluster_id, timestamp=at, resource_key=resource_key)
        trace = store.trace_resource(cluster_id=config.cluster_id, timestamp=at, resource_key=resource_key, max_depth=2)
        obj = snapshot["objects"][0] if snapshot["objects"] else None
        if not obj:
            return {"kind": "resource", "message": f"{_resource_label(resource_key)} did not exist at {at}.", "resource": None, "relations": trace}
        identity = object_identity(obj)
        return {
            "kind": "resource",
            "message": f"At {at}, {identity['kind']}/{identity['name']} existed with {len(trace['edges'])} traced relationship(s). I’ve opened its reconstructed manifest.",
            "resource": obj,
            "relations": trace,
            "options": ["Rewind 60 seconds", "Show changes in window", "Prepare deep investigation"],
        }
    if action in {"reconstruct", "warnings"}:
        diff = store.diff_states(cluster_id=config.cluster_id, from_at=baseline, to_at=at, namespace=namespace)
        context = store.context_shard(cluster_id=config.cluster_id, incident_at=at, lookback=300, namespace=namespace, resource_key=resource_key, limit=60)
        warnings = [event for event in context["kubernetesEvents"] if event["type"] == "Warning"]
        changed = ", ".join(_resource_label(item["resourceKey"]) for item in diff["changes"][:4]) or "no resources"
        return {
            "kind": "reconstruction",
            "message": f"Rewound to {at}. Since the baseline, {diff['changeCount']} resources changed ({changed}) and I found {len(warnings)} warning events in the preceding five minutes.",
            "diff": diff,
            "warnings": warnings[-12:],
            "options": ["Inspect first change", "Prepare deep investigation", "Return to live state"],
        }
    message = str(request.get("message", "")).lower()
    if any(word in message for word in ("why", "root cause", "investigate", "analyze", "analyse")):
        return {
            "kind": "approval_request",
            "message": "I can run the guarded Laya → policy → Qwen investigation for this exact window. It is read-only, takes roughly 10 seconds on the warmed local models, and will create an MLflow trace. Approve the investigation?",
            "approval": {"action": "analyze", "label": "Approve investigation", "mutation": False},
            "options": ["Approve investigation", "Only show deterministic evidence"],
        }
    return {
        "kind": "options",
        "message": "I can reconstruct this point in time immediately, explain the selected object, show warning evidence, or—after your approval—run a deeper local investigation.",
        "options": ["Reconstruct this moment", "Explain selected resource", "Show warning events", "Prepare deep investigation"],
    }


def run_guarded_analysis(store, config, request: dict) -> dict:
    baseline = to_iso(request.get("baselineAt"), "baselineAt")
    incident = to_iso(request.get("incidentAt"), "incidentAt")
    namespace = request.get("namespace", "kravel-demo")
    scenario = request.get("scenario", "karl_ui")
    run_id = str(uuid.uuid4())
    started_at = to_iso()
    wall_started = time.perf_counter()
    tracer = MlflowTracer(config.mlflow_url, config.mlflow_experiment)
    with tracer.span("kravel.karl_investigation", "AGENT", safe_pipeline_inputs(run_id, scenario, namespace, baseline, incident)) as root:
        result = run_incident_pipeline(store, config, tracer, baseline, incident, namespace, scenario)
        root.set_outputs({"route": result["route"], "decision": result["decision"], "review_status": result["reviewStatus"], "incident_shards": len(result["evidence"].get("shards", []))})
    result["stageMetrics"]["mlflow_setup"] = tracer.setup_ms
    result["stageMetrics"]["mlflow_tracing_overhead"] = tracer.overhead_ms
    result["stageMetrics"]["mlflow_trace_flush"] = tracer.flush()
    total_ms = (time.perf_counter() - wall_started) * 1000
    finished_at = to_iso()
    model = f"{result['laya']['model']}+{result['qwen']['model']}" if result["qwen"] else result["laya"]["model"]
    store.record_benchmark_run(
        comparison_id=run_id, cluster_id=config.cluster_id, scenario=scenario, flow="karl_incident_pipeline", provider="local",
        model=model, status="success", started_at=started_at, finished_at=finished_at, total_ms=total_ms,
        evidence_ms=result["stageMetrics"].get("evidence_reconstruction"),
        model_ms=result["stageMetrics"].get("laya_inference", 0) + result["stageMetrics"].get("qwen_inference", 0),
        tool_ms=result["stageMetrics"].get("qwen_temporal_tools", 0), tool_calls=result["qwen"]["toolCalls"] if result["qwen"] else 0,
        confidence=result["laya"].get("confidence"), diagnosis=result["laya"]["diagnosis"], route=result["route"],
        decision=result["decision"], review_status=result["reviewStatus"], stage_metrics=result["stageMetrics"], trace_id=tracer.trace_id,
        error_code="" if tracer.enabled and not tracer.error else "mlflow_unavailable",
    )
    return {"runId": run_id, "traceId": tracer.trace_id, "totalMs": total_ms, "result": result}
