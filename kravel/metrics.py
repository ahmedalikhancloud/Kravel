from __future__ import annotations


DIAGNOSES = ["config_regression", "service_selector_drift", "bad_image_rollout", "scheduling_constraint"]
BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300]


def _escape(value) -> str:
    return str(value or "").replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _labels(**values) -> str:
    return "{" + ",".join(f'{key}="{_escape(value)}"' for key, value in values.items()) + "}"


def prometheus_metrics(store, cluster_id: str) -> str:
    runs = store.benchmark_runs(cluster_id=cluster_id, limit=10_000)
    lines = [
        "# HELP kravel_benchmark_runs_total Recorded incident-pipeline runs.",
        "# TYPE kravel_benchmark_runs_total counter",
    ]
    counts = {}
    latest = {}
    for run in runs:
        counts[(run["flow"], run["status"])] = counts.get((run["flow"], run["status"]), 0) + 1
        latest.setdefault(run["flow"], run)
    for (flow, status), value in sorted(counts.items()):
        lines.append(f"kravel_benchmark_runs_total{_labels(flow=flow, status=status)} {value}")

    lines.extend([
        "# HELP kravel_benchmark_latency_seconds Latest measured latency by flow and phase.",
        "# TYPE kravel_benchmark_latency_seconds gauge",
        "# HELP kravel_benchmark_tool_calls Latest temporal tool-call count.",
        "# TYPE kravel_benchmark_tool_calls gauge",
        "# HELP kravel_pipeline_stage_latency_seconds Latest pipeline-stage latency.",
        "# TYPE kravel_pipeline_stage_latency_seconds gauge",
        "# HELP kravel_pipeline_stage_latency_distribution_seconds Pipeline-stage latency distribution.",
        "# TYPE kravel_pipeline_stage_latency_distribution_seconds histogram",
        "# HELP kravel_pipeline_route_info Latest route, decision, review status, and trace identifier.",
        "# TYPE kravel_pipeline_route_info gauge",
        "# HELP kravel_benchmark_diagnosis_probability Latest Laya probability by incident class.",
        "# TYPE kravel_benchmark_diagnosis_probability gauge",
    ])
    for flow, run in sorted(latest.items()):
        for phase, field in (("end_to_end", "totalMs"), ("evidence", "evidenceMs"), ("model", "modelMs"), ("tools", "toolMs")):
            if run[field] is not None:
                lines.append(f"kravel_benchmark_latency_seconds{_labels(flow=flow, phase=phase)} {float(run[field]) / 1000}")
        lines.append(f"kravel_benchmark_tool_calls{_labels(flow=flow)} {int(run['toolCalls'])}")
        for stage, milliseconds in sorted(run["stageMetrics"].items()):
            lines.append(f"kravel_pipeline_stage_latency_seconds{_labels(flow=flow, stage=stage)} {float(milliseconds) / 1000}")
        lines.append(
            f"kravel_pipeline_route_info{_labels(flow=flow, route=run['route'], decision=run['decision'], review_status=run['reviewStatus'], trace_id=run['traceId'])} 1"
        )
        for diagnosis in DIAGNOSES:
            if diagnosis in run["diagnosis"]:
                lines.append(f"kravel_benchmark_diagnosis_probability{_labels(flow=flow, diagnosis=diagnosis)} {float(run['diagnosis'][diagnosis])}")

    stages = sorted({stage for run in runs for stage in run["stageMetrics"]})
    for stage in stages:
        values = [float(run["stageMetrics"][stage]) / 1000 for run in runs if stage in run["stageMetrics"]]
        for upper in BUCKETS:
            lines.append(f"kravel_pipeline_stage_latency_distribution_seconds_bucket{_labels(stage=stage, le=upper)} {sum(value <= upper for value in values)}")
        lines.append(f"kravel_pipeline_stage_latency_distribution_seconds_bucket{_labels(stage=stage, le='+Inf')} {len(values)}")
        lines.append(f"kravel_pipeline_stage_latency_distribution_seconds_sum{_labels(stage=stage)} {sum(values)}")
        lines.append(f"kravel_pipeline_stage_latency_distribution_seconds_count{_labels(stage=stage)} {len(values)}")
    return "\n".join(lines) + "\n"
