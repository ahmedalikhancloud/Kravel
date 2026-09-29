import { performance } from "node:perf_hooks";

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
  constructor({ url = "", experimentName = "Kravel Local Incident Pipeline", fetchImpl = globalThis.fetch } = {}) {
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

export async function logPipelineToMlflow(client, { pipelineId, scenario, result }) {
  if (!client?.enabled) return { logged: false, mlflowMs: 0 };
  const started = performance.now();
  const experimentId = await client.experimentId();
  const parentRunId = await client.createRun({
    experimentId,
    runName: `pipeline-${pipelineId.slice(0, 8)}`,
    tags: {
      "kravel.pipeline_id": pipelineId,
      "kravel.scenario": scenario,
      "kravel.route": result.route,
      "kravel.review_status": result.reviewStatus
    }
  });
  try {
    for (const [stage, latencyMs] of Object.entries(result.stageMetrics)) {
      const childRunId = await client.createRun({
        experimentId,
        parentRunId,
        runName: stage,
        tags: {
          "kravel.pipeline_id": pipelineId,
          "kravel.stage": stage,
          "kravel.status": "success"
        }
      });
      await client.logRun(childRunId, {
        params: { scenario },
        metrics: { latency_ms: latencyMs }
      });
      await client.finishRun(childRunId, "FINISHED");
    }

    const mlflowMs = performance.now() - started;
    const mlflowChildRunId = await client.createRun({
      experimentId,
      parentRunId,
      runName: "mlflow_logging",
      tags: { "kravel.pipeline_id": pipelineId, "kravel.stage": "mlflow_logging", "kravel.status": "success" }
    });
    await client.logRun(mlflowChildRunId, { params: { scenario }, metrics: { latency_ms: mlflowMs } });
    await client.finishRun(mlflowChildRunId, "FINISHED");
    await client.logRun(parentRunId, {
      params: {
        scenario,
        route: result.route,
        leading_diagnosis: result.decision,
        review_status: result.reviewStatus,
        laya_model: result.laya.model,
        qwen_model: result.qwen?.model ?? "not_invoked"
      },
      metrics: {
        pipeline_without_mlflow_ms: result.stageMetrics.pipeline_without_mlflow,
        mlflow_logging_ms: mlflowMs,
        total_observed_ms: result.stageMetrics.pipeline_without_mlflow + mlflowMs,
        laya_confidence: result.laya.confidence,
        qwen_tool_calls: result.qwen?.toolCalls ?? 0,
        ...Object.fromEntries(Object.entries(result.laya.diagnosis).map(([name, value]) => [`diagnosis.${name}`, value])),
        ...Object.fromEntries(Object.entries(result.stageMetrics).map(([name, value]) => [`stage.${name}_ms`, value]))
      },
      tags: {
        "kravel.guardrail.laya_input": result.guardrails.layaInput.decision,
        "kravel.guardrail.laya_output": result.guardrails.layaOutput.decision,
        "kravel.guardrail.qwen_input": result.guardrails.qwenInput?.decision ?? "not_invoked",
        "kravel.guardrail.qwen_output": result.guardrails.qwenOutput?.decision ?? "not_invoked"
      }
    });
    await client.finishRun(parentRunId, "FINISHED");
    return { logged: true, parentRunId, mlflowMs };
  } catch (error) {
    await client.finishRun(parentRunId, "FAILED").catch(() => {});
    throw error;
  }
}
