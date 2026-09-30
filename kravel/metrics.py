from __future__ import annotations

from datetime import datetime


BUCKETS = [0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300]


def _escape(value) -> str:
    return str(value or "").replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(**values) -> str:
    return "{" + ",".join(f'{key}="{_escape(value)}"' for key, value in values.items()) + "}"


def _histogram(lines: list[str], name: str, values: list[float], labels: dict):
    cumulative = 0
    for bucket in BUCKETS:
        cumulative = sum(1 for value in values if value <= bucket)
        lines.append(f"{name}_bucket{_labels(**labels, le=bucket)} {cumulative}")
    lines.append(f"{name}_bucket{_labels(**labels, le='+Inf')} {len(values)}")
    lines.append(f"{name}_sum{_labels(**labels)} {sum(values)}")
    lines.append(f"{name}_count{_labels(**labels)} {len(values)}")


def prometheus_metrics(store, component: str = "debugger") -> str:
    audit = store.audit_entries(1000)
    runs = store.investigations(1000)
    proposals = store.proposals(500)
    lines = [
        "# HELP kravel_audit_events_total Audited Kravel actions.",
        "# TYPE kravel_audit_events_total counter",
    ]
    counts = {}
    for entry in audit:
        key = (entry["component"], entry["action"], entry["outcome"])
        counts[key] = counts.get(key, 0) + 1
    for (entry_component, action, outcome), count in sorted(counts.items()):
        lines.append(f"kravel_audit_events_total{_labels(component=entry_component, action=action, outcome=outcome)} {count}")

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
    run_counts = {}
    for run in runs:
        run_counts[run["status"]] = run_counts.get(run["status"], 0) + 1
    for status, count in sorted(run_counts.items()):
        lines.append(f"kravel_debug_investigations_total{_labels(status=status)} {count}")
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
            lines.append(f"kravel_debug_latency_seconds{_labels(stage=stage, trace_id=latest['trace_id'])} {float(milliseconds) / 1000}")
        lines.append(f"kravel_debug_tool_calls {int(latest['tool_calls'])}")
        _histogram(lines, "kravel_debug_total_latency_distribution_seconds", [float(run["total_ms"]) / 1000 for run in runs], {})

    lines.extend([
        "# HELP kravel_approval_proposals_total Approval proposals by status.",
        "# TYPE kravel_approval_proposals_total gauge",
        "# HELP kravel_approval_age_seconds Current age of pending approvals.",
        "# TYPE kravel_approval_age_seconds gauge",
        "# HELP kravel_fixes_executed_total Approved fix executions.",
        "# TYPE kravel_fixes_executed_total counter",
    ])
    statuses = {}
    executed = {}
    now = datetime.now().astimezone()
    for proposal in proposals:
        statuses[proposal["status"]] = statuses.get(proposal["status"], 0) + 1
        if proposal["status"] == "pending":
            created = datetime.fromisoformat(proposal["created_at"].replace("Z", "+00:00"))
            lines.append(f"kravel_approval_age_seconds{_labels(proposal_id=proposal['id'][:8], fix_id=proposal['fix_id'])} {max((now - created).total_seconds(), 0)}")
        if proposal["status"] == "executed":
            executed[proposal["fix_id"]] = executed.get(proposal["fix_id"], 0) + 1
    for status, count in sorted(statuses.items()):
        lines.append(f"kravel_approval_proposals_total{_labels(status=status)} {count}")
    for fix_id, count in sorted(executed.items()):
        lines.append(f"kravel_fixes_executed_total{_labels(fix_id=fix_id)} {count}")
    lines.append(f"kravel_component_info{_labels(component=component)} 1")
    return "\n".join(lines) + "\n"
