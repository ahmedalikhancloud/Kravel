from __future__ import annotations

import argparse
import json
import time
import urllib.request
import uuid

from .config import load_config
from .pipeline import run_incident_pipeline
from .reports import render_report
from .store import TemporalStore
from .tracing import MlflowTracer, safe_pipeline_inputs
from .utils import to_iso


def _parser():
    parser = argparse.ArgumentParser(prog="kravel")
    sub = parser.add_subparsers(dest="command", required=True)
    pipeline = sub.add_parser("pipeline", help="run the guarded LangGraph incident workflow")
    pipeline.add_argument("--baseline", required=True)
    pipeline.add_argument("--incident", required=True)
    pipeline.add_argument("--namespace", default="")
    pipeline.add_argument("--scenario", default="unspecified")
    report = sub.add_parser("report", help="print a deterministic time-travel report")
    report.add_argument("--baseline", required=True); report.add_argument("--incident", required=True); report.add_argument("--namespace", default="kravel-demo")
    probe = sub.add_parser("probe-resource", help="check whether the collector observed a resource")
    probe.add_argument("--kind", required=True); probe.add_argument("--name", required=True); probe.add_argument("--namespace", default="kravel-demo"); probe.add_argument("--annotation", default="")
    sub.add_parser("check-llm", help="check Docker Model Runner connectivity")
    return parser


def run_pipeline(args, config, store):
    pipeline_id = str(uuid.uuid4())
    started_at = to_iso()
    wall_started = time.perf_counter()
    tracer = MlflowTracer(config.mlflow_url, config.mlflow_experiment)
    print(f"\nKRAVEL LANGGRAPH INCIDENT PIPELINE {pipeline_id[:8]}\n===========================================", flush=True)
    with tracer.span("kravel.incident_pipeline", "AGENT", safe_pipeline_inputs(pipeline_id, args.scenario, args.namespace, args.baseline, args.incident)) as root:
        result = run_incident_pipeline(store, config, tracer, args.baseline, args.incident, args.namespace, args.scenario, lambda name, _args: print(f"[qwen] {name}", flush=True))
        root.set_outputs({"route": result["route"], "decision": result["decision"], "review_status": result["reviewStatus"], "change_count": result["evidence"]["changeCount"], "event_count": result["evidence"]["eventCount"]})
    result["stageMetrics"]["mlflow_setup"] = tracer.setup_ms
    result["stageMetrics"]["mlflow_tracing_overhead"] = tracer.overhead_ms
    result["stageMetrics"]["mlflow_trace_flush"] = tracer.flush()
    total_ms = (time.perf_counter() - wall_started) * 1000
    finished_at = to_iso()
    model = f"{result['laya']['model']}+{result['qwen']['model']}" if result["qwen"] else result["laya"]["model"]
    store.record_benchmark_run(comparison_id=pipeline_id, cluster_id=config.cluster_id, scenario=args.scenario, flow="incident_pipeline", provider="local", model=model, status="success", started_at=started_at, finished_at=finished_at, total_ms=total_ms, evidence_ms=result["stageMetrics"].get("evidence_reconstruction"), model_ms=result["stageMetrics"].get("laya_inference", 0) + result["stageMetrics"].get("qwen_inference", 0), tool_ms=result["stageMetrics"].get("qwen_temporal_tools", 0), tool_calls=result["qwen"]["toolCalls"] if result["qwen"] else 0, confidence=result["laya"].get("confidence"), diagnosis=result["laya"]["diagnosis"], route=result["route"], decision=result["decision"], review_status=result["reviewStatus"], stage_metrics=result["stageMetrics"], trace_id=tracer.trace_id, error_code="" if tracer.enabled and not tracer.error else "mlflow_unavailable")
    print(f"Route: {result['route']}\nLeading classification: {result['decision']} ({result['policy']['topProbability'] * 100:.1f}%)\nPolicy reasons: {', '.join(result['policy']['reasons'])}\nHuman review: {result['reviewStatus']}")
    print("\nStage latency:")
    for stage, value in result["stageMetrics"].items():
        print(f"  {stage:28} {value:10.3f} ms")
    print(f"  {'observed_total':28} {total_ms:10.3f} ms")
    print("\nGuardrails:")
    for key in ("layaInput", "layaOutput", "qwenInput", "qwenOutput"):
        print(f"  {key:12} {result['guardrails'].get(key, {}).get('decision', 'not_invoked')}")
    if result["qwen"]:
        print("\nQwen evidence-grounded investigation:\n" + result["qwen"]["report"])
    print("\nHuman-review proposal (no mutation executed):\n" + json.dumps(result["proposal"], indent=2))
    if tracer.trace_id:
        print(f"\nMLflow trace: {tracer.trace_id} (open MLflow → Traces)")
    else:
        print(f"\nMLflow tracing unavailable: {tracer.error or 'tracking disabled'}")


def main(argv=None):
    args = _parser().parse_args(argv)
    config = load_config()
    store = TemporalStore(config.db_path)
    try:
        if args.command == "pipeline":
            run_pipeline(args, config, store)
        elif args.command == "report":
            print(render_report(store, config.cluster_id, args.baseline, args.incident, args.namespace))
        elif args.command == "probe-resource":
            state = store.state_at(cluster_id=config.cluster_id, timestamp=to_iso(), namespace=args.namespace)
            obj = next((item for item in state["objects"] if item.get("kind") == args.kind and item.get("metadata", {}).get("name") == args.name), None)
            found = bool(obj) and (not args.annotation or args.annotation in obj.get("metadata", {}).get("annotations", {}).values() or args.annotation in obj.get("spec", {}).get("template", {}).get("metadata", {}).get("annotations", {}).values())
            print("true" if found else "false")
            return 0 if found else 1
        elif args.command == "check-llm":
            endpoint = config.llm_base_url.rstrip("/") + "/models"
            with urllib.request.urlopen(endpoint, timeout=10) as response:
                payload = json.load(response)
            print(f"Qwen endpoint reachable ({len(payload.get('data', []))} models visible)")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
