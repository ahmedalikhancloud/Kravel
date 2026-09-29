import assert from "node:assert/strict";
import test from "node:test";
import { logPipelineToMlflow, MlflowClient } from "../src/mlflow.mjs";

test("logs pipeline and per-stage latency without evidence payloads", async () => {
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

  const logged = await logPipelineToMlflow(client, {
    pipelineId: "safe-pipeline-id",
    scenario: "multi-incident",
    result: {
      route: "qwen_investigation",
      decision: "bad_image_rollout",
      reviewStatus: "awaiting_human_review",
      stageMetrics: { laya_input_guardrail: 1.2, laya_inference: 48, qwen_input_guardrail: 2.1 },
      laya: { model: "english", confidence: 0.9, diagnosis: { bad_image_rollout: 0.95 } },
      qwen: { model: "qwen-test", toolCalls: 2 },
      guardrails: {
        layaInput: { decision: "allow" },
        layaOutput: { decision: "allow" },
        qwenOutput: { decision: "allow_with_warnings" }
      }
    }
  });

  assert.equal(logged.logged, true);
  assert.ok(logged.mlflowMs >= 0);
  assert.equal(requests[0].method, "GET");
  assert.match(requests[0].url, /experiment_name=Kravel/);
  const serialized = JSON.stringify(requests);
  assert.doesNotMatch(serialized, /STARTUP_MODE|Bearer|api[_-]?key|system prompt/i);
  assert.match(serialized, /laya_input_guardrail/);
  assert.match(serialized, /diagnosis\.bad_image_rollout/);
});

test("rejects a remote plain-HTTP MLflow destination", () => {
  assert.throws(() => new MlflowClient({ url: "http://tracking.example.com" }), /must use HTTPS/);
});
