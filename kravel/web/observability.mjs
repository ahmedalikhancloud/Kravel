// Dashboard destinations are deliberately fixed to the local demo. Never take a
// URL from a model, Kubernetes object, tracking configuration, or saved report.
export const MLFLOW_HOME = "http://127.0.0.1:5000/#/experiments";
export const GRAFANA_DASHBOARD = "http://127.0.0.1:3000/d/kravel-guarded-debugger/kravel-guarded-debugger";

const experiment = (value) => typeof value === "string" && /^\d{1,64}$/.test(value);
const trace = (value) => typeof value === "string" && /^tr-[a-f0-9]{32}$/.test(value);
const run = (value) => typeof value === "string" && /^[a-f0-9]{32}$/.test(value);
const session = (value) => typeof value === "string" && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(value);

export function mlflowUrl(view, experimentId, recordId = "") {
  if (!experiment(experimentId)) return "";
  const base = `http://127.0.0.1:5000/#/experiments/${experimentId}`;
  switch (view) {
    case "experiment": return `${base}/overview/usage`;
    case "traces": return `${base}/traces`;
    case "trace": return trace(recordId) ? `${base}/traces?traceId=${recordId}` : "";
    case "sessions": return `${base}/chat-sessions`;
    case "session": return session(recordId) ? `${base}/chat-sessions/${recordId}` : "";
    case "evaluations": return `${base}/evaluation-runs`;
    case "evaluation": return run(recordId) ? `${base}/runs/${recordId}/evaluations` : "";
    case "reports": return run(recordId) ? `${base}/runs/${recordId}/artifacts` : "";
    default: return "";
  }
}

export function requestTraceUrl(request) {
  return mlflowUrl("trace", request?.payload?.experimentId, request?.payload?.traceId);
}

export function chooseEvaluation(jobs, selectedId) {
  // Polling must not jump from a deliberately selected earlier Quick/All run.
  return jobs.find((job) => job.id === selectedId) || jobs[0] || null;
}

export function evaluationCounts(job) {
  return Object.fromEntries(["completed", "error", "skipped"].map((status) => [status, (job?.scorers || []).filter((row) => row.status === status).length]));
}

export function evaluationSourceExperiment(job, request) {
  // An evaluation can live in a different experiment from its source. Old jobs
  // lack sourceExperimentId; only fall back to the matching recorded request.
  return job?.sourceExperimentId || (job?.traceId && job.traceId === request?.payload?.traceId ? request.payload.experimentId : "");
}
