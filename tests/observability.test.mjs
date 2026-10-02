import test from "node:test";
import assert from "node:assert/strict";
import { MLFLOW_HOME, GRAFANA_DASHBOARD, mlflowUrl, requestTraceUrl, chooseEvaluation, evaluationCounts, evaluationSourceExperiment } from "../kravel/web/observability.mjs";

const traceId = `tr-${"a".repeat(32)}`, runId = "b".repeat(32);
const request = {payload: {experimentId: "37", traceId}};

test("deep links use actual experiment IDs and verified MLflow destinations", () => {
  const base = "http://127.0.0.1:5000/#/experiments/37";
  assert.equal(mlflowUrl("trace", "37", traceId), `${base}/traces?traceId=${traceId}`);
  assert.equal(requestTraceUrl(request), `${base}/traces?traceId=${traceId}`);
  assert.equal(mlflowUrl("evaluation", "37", runId), `${base}/runs/${runId}/evaluations`);
  assert.equal(mlflowUrl("reports", "37", runId), `${base}/runs/${runId}/artifacts`);
  assert.equal(mlflowUrl("evaluations", "37"), `${base}/evaluation-runs`);
  assert.equal(mlflowUrl("experiment", "37"), `${base}/overview/usage`);
  assert.equal(mlflowUrl("traces", "37"), `${base}/traces`);
  assert.equal(mlflowUrl("sessions", "37"), `${base}/chat-sessions`);
  const sessionId = "12345678-1234-1234-1234-123456789abc";
  assert.equal(mlflowUrl("session", "37", sessionId), `${base}/chat-sessions/${sessionId}`);
});

test("missing record metadata never guesses experiment 1 or an undefined URL", () => {
  for (const value of [undefined, null, "", "undefined", "null", 37, true]) assert.equal(mlflowUrl("trace", value, traceId), "");
  assert.equal(requestTraceUrl({payload: {traceId}}), "");
  assert.equal(requestTraceUrl({payload: {experimentId: "37"}}), "");
  assert.equal(requestTraceUrl(null), "");
  assert.equal(mlflowUrl("evaluation", "37"), "");
  assert.equal(mlflowUrl("trace", "37", "not-recorded"), "");
  assert.equal(mlflowUrl("unknown", "37", runId), "");
});

test("log/model/config strings cannot introduce remote URLs, private queries or route injection", () => {
  for (const value of ["https://external.invalid", "37?token=private", "37/../../", "37#private", "javascript:alert(1)", "1\n", "<img>", "1".repeat(65)]) {
    assert.equal(mlflowUrl("traces", value), "");
    assert.equal(mlflowUrl("trace", "37", value), "");
    assert.equal(mlflowUrl("reports", "37", value), "");
  }
  assert.equal(mlflowUrl("trace", "37", `${traceId}&token=private`), "");
  assert.equal(mlflowUrl("session", "37", ".."), "");
  for (const url of [MLFLOW_HOME, GRAFANA_DASHBOARD, requestTraceUrl(request)]) {
    assert.equal(new URL(url).hostname, "127.0.0.1");
    assert.equal(new URL(url).username + new URL(url).password, "");
    assert.ok(!url.includes("token="));
  }
});

test("evaluation history selection survives polling and falls back only for absent jobs", () => {
  const older = {id: "older", status: "completed"}, latest = {id: "latest", status: "running"};
  assert.equal(chooseEvaluation([latest, older], "older"), older);
  assert.equal(chooseEvaluation([latest, older], "missing"), latest);
  assert.equal(chooseEvaluation([], "older"), null);
  assert.deepEqual(evaluationCounts({scorers: [{status: "completed", feedback: [{feedback: {value: "no"}}]}, {status: "error"}, {status: "skipped"}]}), {completed: 1, error: 1, skipped: 1});
  assert.deepEqual(evaluationCounts(null), {completed: 0, error: 0, skipped: 0});
});

test("source, evaluation, and judge experiments are never conflated", () => {
  assert.equal(evaluationSourceExperiment({sourceExperimentId: "8", experimentId: "12", traceId}, request), "8");
  assert.equal(evaluationSourceExperiment({experimentId: "12", traceId}, request), "37");
  assert.equal(evaluationSourceExperiment({experimentId: "12", traceId: `tr-${"c".repeat(32)}`}, request), "");
  assert.equal(evaluationSourceExperiment({experimentId: "12"}, null), "");
});
