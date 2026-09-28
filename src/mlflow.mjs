function trackingUrl(value) {
  if (!value) return null;
  const url = new URL(value);
  if (!["https:", "http:"].includes(url.protocol)) throw new Error("MLflow URL must use http or https");
  const internal = ["localhost", "127.0.0.1", "::1"].includes(url.hostname)
    || url.hostname.endsWith(".svc")
    || url.hostname.endsWith(".svc.cluster.local")
    || !url.hostname.includes(".");
  if (url.protocol === "http:" && !internal) throw new Error("Remote MLflow must use HTTPS");
  url.pathname = url.pathname.replace(/\/+$/, "");
  return url;
}

function cleanKey(value) {
  return String(value).replace(/[^a-zA-Z0-9_.-]/g, "_").slice(0, 250);
}

export class MlflowClient {
  constructor({ url = "", experimentName = "Kravel Incident Flow Comparison", fetchImpl = globalThis.fetch } = {}) {
    this.url = trackingUrl(url);
    this.experimentName = experimentName;
    this.fetchImpl = fetchImpl;
  }

  get enabled() {
    return Boolean(this.url);
  }

  async request(method, path, body) {
    if (!this.enabled) throw new Error("MLflow is disabled");
    const target = new URL(this.url);
    target.pathname = `${this.url.pathname.replace(/\/+$/, "")}${path}`;
    if (method === "GET") {
      for (const [key, value] of Object.entries(body ?? {})) target.searchParams.set(key, value);
    }
    let response;
    try {
      response = await this.fetchImpl(target, {
        method,
        headers: { "content-type": "application/json", "user-agent": "kravel/0.1.0" },
        ...(method === "GET" ? {} : { body: JSON.stringify(body) }),
        signal: AbortSignal.timeout(10_000)
      });
    } catch {
      throw new Error("MLflow request failed");
    }
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(`MLflow request returned HTTP ${response.status}`);
      error.statusCode = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  async experimentId() {
    try {
      const result = await this.request("GET", "/api/2.0/mlflow/experiments/get-by-name", { experiment_name: this.experimentName });
      if (result.experiment?.experiment_id) return result.experiment.experiment_id;
    } catch (error) {
      if (error.statusCode !== 404 && error.payload?.error_code !== "RESOURCE_DOES_NOT_EXIST") throw error;
    }
    const created = await this.request("POST", "/api/2.0/mlflow/experiments/create", { name: this.experimentName });
    return created.experiment_id;
  }

  async createRun({ experimentId, runName, tags = {}, parentRunId = "" }) {
    const allTags = {
      "mlflow.runName": runName,
      ...tags,
      ...(parentRunId ? { "mlflow.parentRunId": parentRunId } : {})
    };
    const result = await this.request("POST", "/api/2.0/mlflow/runs/create", {
      experiment_id: experimentId,
      start_time: Date.now(),
      tags: Object.entries(allTags).map(([key, value]) => ({ key: cleanKey(key), value: String(value).slice(0, 5000) }))
    });
    const runId = result.run?.info?.run_id;
    if (!runId) throw new Error("MLflow did not return a run id");
    return runId;
  }

  async logRun(runId, { metrics = {}, params = {}, tags = {} } = {}) {
    const timestamp = Date.now();
    await this.request("POST", "/api/2.0/mlflow/runs/log-batch", {
      run_id: runId,
      metrics: Object.entries(metrics)
        .filter(([, value]) => Number.isFinite(Number(value)))
        .map(([key, value]) => ({ key: cleanKey(key), value: Number(value), timestamp, step: 0 })),
      params: Object.entries(params).map(([key, value]) => ({ key: cleanKey(key), value: String(value).slice(0, 500) })),
      tags: Object.entries(tags).map(([key, value]) => ({ key: cleanKey(key), value: String(value).slice(0, 5000) }))
    });
  }

  async finishRun(runId, status = "FINISHED") {
    await this.request("POST", "/api/2.0/mlflow/runs/update", { run_id: runId, status, end_time: Date.now() });
  }
}

export async function logComparisonToMlflow(client, { comparisonId, scenario, runs }) {
  if (!client?.enabled) return { logged: false };
  const experimentId = await client.experimentId();
  const parentRunId = await client.createRun({
    experimentId,
    runName: `comparison-${comparisonId.slice(0, 8)}`,
    tags: { "kravel.comparison_id": comparisonId, "kravel.scenario": scenario }
  });
  try {
    for (const run of runs) {
      const childRunId = await client.createRun({
        experimentId,
        parentRunId,
        runName: run.flow,
        tags: {
          "kravel.comparison_id": comparisonId,
          "kravel.flow": run.flow,
          "kravel.status": run.status
        }
      });
      await client.logRun(childRunId, {
        params: { provider: run.provider, model: run.model, scenario },
        metrics: {
          total_ms: run.totalMs,
          evidence_ms: run.evidenceMs,
          model_ms: run.modelMs,
          tool_ms: run.toolMs,
          tool_calls: run.toolCalls,
          confidence: run.confidence,
          ...Object.fromEntries(Object.entries(run.diagnosis ?? {}).map(([name, value]) => [`diagnosis.${name}`, value]))
        },
        tags: { "kravel.error_code": run.errorCode || "none" }
      });
      await client.finishRun(childRunId, run.status === "success" ? "FINISHED" : "FAILED");
    }
    await client.finishRun(parentRunId, "FINISHED");
  } catch (error) {
    await client.finishRun(parentRunId, "FAILED").catch(() => {});
    throw error;
  }
  return { logged: true, parentRunId };
}
