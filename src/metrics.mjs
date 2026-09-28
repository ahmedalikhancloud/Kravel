const diagnosisNames = [
  "config_regression",
  "service_selector_drift",
  "bad_image_rollout",
  "scheduling_constraint"
];

const latencyBuckets = [0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120];
const latencyPhases = [
  ["end_to_end", "totalMs"],
  ["evidence", "evidenceMs"],
  ["model", "modelMs"],
  ["tools", "toolMs"]
];

function label(value) {
  return String(value ?? "").replace(/\\/g, "\\\\").replace(/\n/g, "\\n").replace(/"/g, '\\"');
}

function number(value) {
  return Number.isFinite(Number(value)) ? Number(value) : 0;
}

function labels(values) {
  return `{${Object.entries(values).map(([key, value]) => `${key}="${label(value)}"`).join(",")}}`;
}

export function prometheusMetrics(store, clusterId) {
  const runs = store.benchmarkRuns({ clusterId, limit: 10_000 });
  const counts = new Map();
  const latest = new Map();
  for (const run of runs) {
    const countKey = `${run.flow}\u0000${run.status}`;
    counts.set(countKey, (counts.get(countKey) ?? 0) + 1);
    if (!latest.has(run.flow)) latest.set(run.flow, run);
  }

  const lines = [
    "# HELP kravel_benchmark_runs_total Number of recorded comparison flow runs.",
    "# TYPE kravel_benchmark_runs_total counter"
  ];
  for (const [key, value] of [...counts.entries()].sort()) {
    const [flow, status] = key.split("\u0000");
    lines.push(`kravel_benchmark_runs_total${labels({ flow, status })} ${value}`);
  }

  lines.push(
    "# HELP kravel_benchmark_latency_distribution_seconds Distribution of successful comparison latency by flow and phase.",
    "# TYPE kravel_benchmark_latency_distribution_seconds histogram"
  );
  const flows = [...new Set(runs.map((run) => run.flow))].sort();
  for (const flow of flows) {
    const successful = runs.filter((run) => run.flow === flow && run.status === "success");
    for (const [phase, field] of latencyPhases) {
      const values = successful
        .map((run) => run[field])
        .filter((value) => value !== null && Number.isFinite(Number(value)))
        .map((value) => Number(value) / 1000);
      if (!values.length) continue;
      for (const upperBound of latencyBuckets) {
        lines.push(`kravel_benchmark_latency_distribution_seconds_bucket${labels({ flow, phase, le: upperBound })} ${values.filter((value) => value <= upperBound).length}`);
      }
      lines.push(`kravel_benchmark_latency_distribution_seconds_bucket${labels({ flow, phase, le: "+Inf" })} ${values.length}`);
      lines.push(`kravel_benchmark_latency_distribution_seconds_sum${labels({ flow, phase })} ${values.reduce((sum, value) => sum + value, 0)}`);
      lines.push(`kravel_benchmark_latency_distribution_seconds_count${labels({ flow, phase })} ${values.length}`);
    }
  }

  lines.push(
    "# HELP kravel_benchmark_latest_info Metadata and status for the latest run of each flow.",
    "# TYPE kravel_benchmark_latest_info gauge",
    "# HELP kravel_benchmark_latency_seconds Latest measured latency by flow and phase.",
    "# TYPE kravel_benchmark_latency_seconds gauge",
    "# HELP kravel_benchmark_tool_calls Latest temporal tool-call count.",
    "# TYPE kravel_benchmark_tool_calls gauge",
    "# HELP kravel_benchmark_diagnosis_probability Latest Laya probability by incident class.",
    "# TYPE kravel_benchmark_diagnosis_probability gauge",
    "# HELP kravel_benchmark_confidence Latest aggregate classifier confidence.",
    "# TYPE kravel_benchmark_confidence gauge"
  );

  for (const run of [...latest.values()].sort((left, right) => left.flow.localeCompare(right.flow))) {
    const common = { flow: run.flow, provider: run.provider, model: run.model, status: run.status };
    lines.push(`kravel_benchmark_latest_info${labels(common)} 1`);
    for (const [phase, milliseconds] of [
      ["end_to_end", run.totalMs],
      ["evidence", run.evidenceMs],
      ["model", run.modelMs],
      ["tools", run.toolMs]
    ]) {
      if (milliseconds !== null) lines.push(`kravel_benchmark_latency_seconds${labels({ flow: run.flow, phase })} ${number(milliseconds) / 1000}`);
    }
    lines.push(`kravel_benchmark_tool_calls${labels({ flow: run.flow })} ${number(run.toolCalls)}`);
    if (run.confidence !== null) lines.push(`kravel_benchmark_confidence${labels({ flow: run.flow })} ${number(run.confidence)}`);
    for (const diagnosis of diagnosisNames) {
      if (run.diagnosis[diagnosis] !== undefined) {
        lines.push(`kravel_benchmark_diagnosis_probability${labels({ flow: run.flow, diagnosis })} ${number(run.diagnosis[diagnosis])}`);
      }
    }
  }
  return `${lines.join("\n")}\n`;
}
