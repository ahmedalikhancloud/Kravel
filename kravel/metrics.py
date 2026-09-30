from __future__ import annotations

from datetime import datetime


BUCKETS = [0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300]


def _escape(value) -> str:
    return str(value or "").replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(**values) -> str:
    return "{" + ",".join(f'{key}="{_escape(value)}"' for key, value in values.items()) + "}"


def prometheus_metrics(store, component: str = "debugger") -> str:
    summary = store.metric_summary(BUCKETS)
    runs = store.investigations(1)
    proposals = store.active_proposals()
    lines = [
        "# HELP kravel_audit_events_total Audited Kravel actions.",
        "# TYPE kravel_audit_events_total counter",
    ]
    for entry in summary["audit"]:
        lines.append(f"kravel_audit_events_total{_labels(component=entry['component'], action=entry['action'], outcome=entry['outcome'])} {entry['count']}")

    lines.extend([
        "# HELP kravel_debug_investigations_total Debugger investigations by status.",
        "# TYPE kravel_debug_investigations_total counter",
        "# HELP kravel_debug_latency_seconds Latest debugger-stage latency.",
        "# TYPE kravel_debug_latency_seconds gauge",
        "# HELP kravel_debug_tool_calls Latest read-only tool call count.",
        "# TYPE kravel_debug_tool_calls gauge",
        "# HELP kravel_debug_total_latency_distribution_seconds End-to-end debugger latency.",
        "# TYPE kravel_debug_total_latency_distribution_seconds histogram",
    ])
    for run in summary["runs"]:
        lines.append(f"kravel_debug_investigations_total{_labels(status=run['status'])} {run['count']}")
    if runs:
        latest = runs[0]
        stages = {
            "end_to_end": latest["total_ms"],
            "qwen_inference": latest["model_ms"],
            "read_tools": latest["tool_ms"],
            "input_guardrail": latest["input_guardrail_ms"],
            "output_guardrail": latest["output_guardrail_ms"],
            "mlflow_setup": latest["mlflow_setup_ms"],
            "mlflow_span_overhead": latest["mlflow_overhead_ms"],
            "mlflow_trace_flush": latest["mlflow_flush_ms"],
        }
        for stage, milliseconds in stages.items():
            lines.append(f"kravel_debug_latency_seconds{_labels(stage=stage)} {float(milliseconds) / 1000}")
        lines.append(f"kravel_debug_tool_calls {int(latest['tool_calls'])}")
        histogram_name = "kravel_debug_total_latency_distribution_seconds"
        for bucket, count in zip(BUCKETS, summary["buckets"]):
            lines.append(f"{histogram_name}_bucket{_labels(le=bucket)} {count}")
        lines.append(f"{histogram_name}_bucket{_labels(le='+Inf')} {summary['latency']['count']}")
        lines.append(f"{histogram_name}_sum {summary['latency']['sum']}")
        lines.append(f"{histogram_name}_count {summary['latency']['count']}")

    lines.extend([
        "# HELP kravel_approval_proposals_total Approval proposals by status.",
        "# TYPE kravel_approval_proposals_total gauge",
        "# HELP kravel_approval_age_seconds Current age of pending approvals.",
        "# TYPE kravel_approval_age_seconds gauge",
        "# HELP kravel_fixes_executed_total Approved fix executions.",
        "# TYPE kravel_fixes_executed_total counter",
    ])
    now = datetime.now().astimezone()
    for proposal in proposals:
        if proposal["status"] == "pending":
            created = datetime.fromisoformat(proposal["created_at"].replace("Z", "+00:00"))
            lines.append(f"kravel_approval_age_seconds{_labels(proposal_id=proposal['id'][:8], fix_id=proposal['fix_id'])} {max((now - created).total_seconds(), 0)}")
    for proposal in summary["proposals"]:
        lines.append(f"kravel_approval_proposals_total{_labels(status=proposal['status'])} {proposal['count']}")
    for fix in summary["fixes"]:
        lines.append(f"kravel_fixes_executed_total{_labels(fix_id=fix['fix_id'])} {fix['count']}")
    lines.append(f"kravel_component_info{_labels(component=component)} 1")
    lines.extend(["# HELP kravel_workflow_step_seconds_sum Persisted workflow stage duration.", "# TYPE kravel_workflow_step_seconds_sum counter", "# TYPE kravel_workflow_step_seconds_count counter"])
    for step in store.workflow_metrics():
        # Step keys are code-owned, bounded stages (never resource names or run IDs).
        labels = _labels(component=component, workflow=step["kind"], stage=step["step_key"], outcome=step["status"])
        lines.append(f"kravel_workflow_step_seconds_sum{labels} {step['seconds'] or 0}")
        lines.append(f"kravel_workflow_step_seconds_count{labels} {step['count']}")
    return "\n".join(lines) + "\n"
