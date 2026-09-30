from __future__ import annotations

import time
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .agent import run_temporal_agent
from .guardrails import guard_laya_output, guard_model_input, guard_qwen_output
from .laya import build_classifier_evidence, run_laya_classifier
from .policy import create_human_review_proposal, decide_policy, run_predefined_automation


class PipelineState(TypedDict, total=False):
    baseline_at: str
    incident_at: str
    namespace: str
    scenario: str
    evidence: dict
    laya_input: dict
    laya: dict
    policy: dict
    predefined_automation: dict | None
    qwen: dict | None
    qwen_output: dict | None
    proposal: dict
    stage_metrics: dict
    guardrails: dict


def _elapsed(started):
    return (time.perf_counter() - started) * 1000


def _qwen_question(policy, diagnosis):
    probabilities = ", ".join(f"{name}={value:.3f}" for name, value in sorted(diagnosis.items(), key=lambda item: item[1], reverse=True))
    return f"Independently investigate the complete incident window because the policy gate selected deep analysis. Laya is only a fallible routing signal, not evidence: {probabilities}. Policy reasons: {', '.join(policy['reasons'])}. Prefer exact spec/config mutations and later Warning Events over status churn. Identify independent failures and do not propose or execute mutations."


def run_incident_pipeline(store, config, tracer, baseline_at, incident_at, namespace="", scenario="unspecified", on_tool_call=lambda _name, _args: None):
    pipeline_started = time.perf_counter()
    initial: PipelineState = {"baseline_at": baseline_at, "incident_at": incident_at, "namespace": namespace, "scenario": scenario, "stage_metrics": {}, "guardrails": {}}

    def reconstruct(state):
        started = time.perf_counter()
        with tracer.span("langgraph.reconstruct_evidence", "RETRIEVER", {"namespace": namespace or "all", "baseline_at": baseline_at, "incident_at": incident_at}) as span:
            evidence = build_classifier_evidence(store, config.cluster_id, baseline_at, incident_at, namespace)
            span.set_outputs({"change_count": evidence["changeCount"], "event_count": evidence["eventCount"]})
        return {"evidence": evidence, "stage_metrics": {**state["stage_metrics"], "evidence_reconstruction": _elapsed(started)}}

    def laya_input_guardrail(state):
        with tracer.span("langgraph.laya_input_guardrail", "GUARDRAIL", {"input_characters": len(state["evidence"]["state"])}) as span:
            guarded = guard_model_input(state["evidence"]["state"], "laya", 1600)
            span.set_outputs({"decision": guarded["decision"], "finding_count": len(guarded["findings"])})
        return {"laya_input": guarded, "stage_metrics": {**state["stage_metrics"], "laya_input_guardrail": guarded["latencyMs"]}, "guardrails": {**state["guardrails"], "layaInput": {"decision": guarded["decision"], "findings": guarded["findings"]}}}

    def classify_laya(state):
        started = time.perf_counter()
        with tracer.span("langgraph.laya_classifier", "LLM", {"model": config.laya_model, "evidence_characters": len(state["laya_input"]["value"])}) as span:
            raw = run_laya_classifier(config.laya_url, config.laya_api_key, config.laya_model, state["laya_input"]["value"])
            span.set_outputs({"latency_ms": raw["modelMs"], "classes": len(raw["diagnosis"])})
        return {"laya": raw, "stage_metrics": {**state["stage_metrics"], "laya_inference": _elapsed(started)}}

    def laya_output_guardrail(state):
        with tracer.span("langgraph.laya_output_guardrail", "GUARDRAIL", {"model": state["laya"]["model"]}) as span:
            guarded = guard_laya_output(state["laya"])
            span.set_outputs({"decision": guarded["decision"]})
        return {"laya": guarded["value"], "stage_metrics": {**state["stage_metrics"], "laya_output_guardrail": guarded["latencyMs"]}, "guardrails": {**state["guardrails"], "layaOutput": {"decision": guarded["decision"], "findings": guarded["findings"]}}}

    def policy_gate(state):
        with tracer.span("langgraph.policy_gate", "CHAIN", {"classification_count": len(state["laya"]["diagnosis"])}) as span:
            policy = decide_policy(state["laya"], config.policy)
            span.set_outputs({"route": policy["route"], "leading_diagnosis": policy["topDiagnosis"], "top_probability": policy["topProbability"]})
        return {"policy": policy, "stage_metrics": {**state["stage_metrics"], "policy_gate": policy["latencyMs"]}}

    def route(state):
        return "predefined_runbook" if state["policy"]["route"] == "predefined_runbook" else "qwen_investigation"

    def predefined_runbook(state):
        with tracer.span("langgraph.predefined_read_only_runbook", "TOOL", {"diagnosis": state["policy"]["topDiagnosis"]}) as span:
            result = run_predefined_automation(store, config.cluster_id, baseline_at, incident_at, namespace, state["policy"]["topDiagnosis"])
            span.set_outputs({"matched_changes": len(result["value"]["matchedChanges"]), "matched_events": len(result["value"]["matchedEvents"]), "remediation_executed": False})
        return {"predefined_automation": result["value"], "stage_metrics": {**state["stage_metrics"], "predefined_automation": result["latencyMs"]}}

    def qwen_investigation(state):
        started = time.perf_counter()
        with tracer.span("langgraph.qwen_agent", "AGENT", {"model": config.llm_model, "policy_reasons": state["policy"]["reasons"]}) as span:
            qwen = run_temporal_agent(store, config, baseline_at, incident_at, namespace, _qwen_question(state["policy"], state["laya"]["diagnosis"]), tracer, on_tool_call)
            span.set_outputs({"tool_calls": qwen["toolCalls"], "model_latency_ms": qwen["modelMs"], "report_characters": len(qwen["answer"])})
        metrics = {**state["stage_metrics"], "qwen_agent_total": _elapsed(started), "qwen_input_guardrail": qwen["inputGuardrailMs"], "qwen_inference": qwen["modelMs"], "qwen_temporal_tools": qwen["toolMs"]}
        guardrails = {**state["guardrails"], "qwenInput": qwen["inputGuardrail"]}
        return {"qwen": qwen, "stage_metrics": metrics, "guardrails": guardrails}

    def qwen_output_guardrail(state):
        with tracer.span("langgraph.qwen_output_guardrail", "GUARDRAIL", {"output_characters": len(state["qwen"]["answer"])}) as span:
            guarded = guard_qwen_output(state["qwen"]["answer"])
            span.set_outputs({"decision": guarded["decision"], "finding_count": len(guarded["findings"])})
        return {"qwen_output": guarded, "stage_metrics": {**state["stage_metrics"], "qwen_output_guardrail": guarded["latencyMs"]}, "guardrails": {**state["guardrails"], "qwenOutput": {"decision": guarded["decision"], "findings": guarded["findings"]}}}

    def human_review(state):
        with tracer.span("langgraph.human_review_package", "CHAIN", {"route": state["policy"]["route"]}) as span:
            proposal = create_human_review_proposal(state["policy"], state["laya"]["diagnosis"], state.get("predefined_automation"), state.get("qwen_output", {}).get("value", ""), state.get("qwen_output"))
            span.set_outputs({"status": proposal["value"]["status"], "remediation_executed": False})
        metrics = {**state["stage_metrics"], "human_review_package": proposal["latencyMs"], "langgraph_pipeline_total": _elapsed(pipeline_started)}
        return {"proposal": proposal["value"], "stage_metrics": metrics}

    builder = StateGraph(PipelineState)
    for name, node in [("reconstruct_evidence", reconstruct), ("laya_input_guardrail", laya_input_guardrail), ("laya_classifier", classify_laya), ("laya_output_guardrail", laya_output_guardrail), ("policy_gate", policy_gate), ("predefined_runbook", predefined_runbook), ("qwen_investigation", qwen_investigation), ("qwen_output_guardrail", qwen_output_guardrail), ("human_review", human_review)]:
        builder.add_node(name, node)
    builder.add_edge(START, "reconstruct_evidence")
    builder.add_edge("reconstruct_evidence", "laya_input_guardrail")
    builder.add_edge("laya_input_guardrail", "laya_classifier")
    builder.add_edge("laya_classifier", "laya_output_guardrail")
    builder.add_edge("laya_output_guardrail", "policy_gate")
    builder.add_conditional_edges("policy_gate", route, {"predefined_runbook": "predefined_runbook", "qwen_investigation": "qwen_investigation"})
    builder.add_edge("predefined_runbook", "human_review")
    builder.add_edge("qwen_investigation", "qwen_output_guardrail")
    builder.add_edge("qwen_output_guardrail", "human_review")
    builder.add_edge("human_review", END)
    state = builder.compile().invoke(initial)
    qwen = state.get("qwen")
    qwen_output = state.get("qwen_output")
    return {"scenario": scenario, "status": "success", "route": state["policy"]["route"], "decision": state["policy"]["topDiagnosis"], "reviewStatus": state["proposal"]["status"], "stageMetrics": state["stage_metrics"], "guardrails": state["guardrails"], "evidence": {"changeCount": state["evidence"]["changeCount"], "eventCount": state["evidence"]["eventCount"]}, "laya": state["laya"], "policy": state["policy"], "predefinedAutomation": state.get("predefined_automation"), "qwen": {"model": qwen["model"], "turns": qwen["turns"], "toolCalls": qwen["toolCalls"], "inputGuardrail": qwen["inputGuardrail"], "report": qwen_output["value"]} if qwen else None, "proposal": state["proposal"]}
