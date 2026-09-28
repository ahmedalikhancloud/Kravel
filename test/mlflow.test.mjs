import assert from "node:assert/strict";
import test from "node:test";
import { logComparisonToMlflow, MlflowClient } from "../src/mlflow.mjs";

test("logs paired flow metrics to MLflow without payload evidence", async () => {
  const requests = [];
  let runNumber = 0;
  const client = new MlflowClient({
    url: "http://kravel-mlflow.kravel-observability.svc.cluster.local:5000",
    fetchImpl: async (url, options) => {
      const body = options.body ? JSON.parse(options.body) : null;
      requests.push({ url: String(url), method: options.method, body });
      if (String(url).includes("get-by-name")) {
        return new Response(JSON.stringify({ experiment: { experiment_id: "7" } }), { status: 200 });
      }
      if (String(url).endsWith("/runs/create")) {
        runNumber += 1;
        return new Response(JSON.stringify({ run: { info: { run_id: `run-${runNumber}` } } }), { status: 200 });
      }
      return new Response("{}", { status: 200 });
    }
  });

  await logComparisonToMlflow(client, {
    comparisonId: "safe-comparison-id",
    scenario: "multi-incident",
    runs: [{
      flow: "laya_classifier",
      provider: "laya",
      model: "english",
      status: "success",
      totalMs: 50,
      evidenceMs: 2,
      modelMs: 48,
      toolMs: null,
      toolCalls: 0,
      confidence: 0.9,
      diagnosis: { config_regression: 0.95 },
      errorCode: ""
    }]
  });

  assert.equal(requests[0].method, "GET");
  assert.match(requests[0].url, /experiment_name=Kravel/);
  const serialized = JSON.stringify(requests);
  assert.doesNotMatch(serialized, /STARTUP_MODE|Bearer|api[_-]?key|trycloudflare/i);
  assert.match(serialized, /diagnosis\.config_regression/);
});

test("rejects a remote plain-HTTP MLflow destination", () => {
  assert.throws(() => new MlflowClient({ url: "http://tracking.example.com" }), /must use HTTPS/);
});
