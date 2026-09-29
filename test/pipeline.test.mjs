import assert from "node:assert/strict";
import test from "node:test";
import { runIncidentPipeline } from "../src/pipeline.mjs";
import { TemporalStore } from "../src/store.mjs";

function seedStore() {
  const store = new TemporalStore(":memory:");
  store.recordResourceChange({
    clusterId: "local-test",
    action: "ADDED",
    eventAt: "2026-09-29T12:00:00Z",
    object: { apiVersion: "v1", kind: "ConfigMap", metadata: { namespace: "shop", name: "api", resourceVersion: "1" }, data: { STARTUP_MODE: "healthy" } }
  });
  store.recordResourceChange({
    clusterId: "local-test",
    action: "MODIFIED",
    eventAt: "2026-09-29T12:01:00Z",
    object: { apiVersion: "v1", kind: "ConfigMap", metadata: { namespace: "shop", name: "api", resourceVersion: "2" }, data: { STARTUP_MODE: "broken" } }
  });
  return store;
}

function layaResponse(overrides = {}) {
  const values = {
    config_regression: 0.97,
    service_selector_drift: 0.03,
    bad_image_rollout: 0.02,
    scheduling_constraint: 0.01,
    ...overrides
  };
  return new Response(JSON.stringify({
    answers: Object.fromEntries(Object.entries(values).map(([name, noul]) => [name, { noul, answer_confidence: 0.98 }]))
  }), { status: 200, headers: { "content-type": "application/json" } });
}

function config() {
  return {
    clusterId: "local-test",
    layaUrl: "http://127.0.0.1:8000",
    layaApiKey: "",
    layaModel: "english",
    llmBaseUrl: "http://127.0.0.1:12434/v1",
    llmModel: "qwen-test",
    llmApiKey: "",
    llmMaxTurns: 4,
    policy: { highConfidence: 0.85, minimumMargin: 0.2, positiveThreshold: 0.65 }
  };
}

test("high-confidence routine incident stops at the predefined runbook and human review", async () => {
  const store = seedStore();
  let qwenCalls = 0;
  try {
    const result = await runIncidentPipeline({
      store,
      config: config(),
      embeddingClient: { enabled: false },
      baselineAt: "2026-09-29T12:00:30Z",
      incidentAt: "2026-09-29T12:02:00Z",
      namespace: "shop",
      fetchImpl: async (url) => {
        if (String(url).includes("systemone")) return layaResponse();
        qwenCalls += 1;
        throw new Error("Qwen should not be invoked");
      }
    });
    assert.equal(result.route, "predefined_runbook");
    assert.equal(result.reviewStatus, "awaiting_human_review");
    assert.equal(result.proposal.diagnosticAutomationExecuted, true);
    assert.equal(result.proposal.remediationExecuted, false);
    assert.equal(result.predefinedAutomation.executionMode, "read_only_diagnostics");
    assert.deepEqual(result.predefinedAutomation.checksExecuted, ["temporal_state_diff", "incident_event_correlation"]);
    assert.equal(result.qwen, null);
    assert.equal(qwenCalls, 0);
    assert.ok(result.stageMetrics.laya_input_guardrail >= 0);
    assert.ok(result.stageMetrics.laya_output_guardrail >= 0);
  } finally {
    store.close();
  }
});

test("severe incident escalates through guarded Qwen tool use", async () => {
  const store = seedStore();
  let qwenCalls = 0;
  try {
    const result = await runIncidentPipeline({
      store,
      config: config(),
      embeddingClient: { enabled: false },
      baselineAt: "2026-09-29T12:00:30Z",
      incidentAt: "2026-09-29T12:02:00Z",
      namespace: "shop",
      fetchImpl: async (url) => {
        if (String(url).includes("systemone")) {
          return layaResponse({ config_regression: 0.1, bad_image_rollout: 0.96 });
        }
        qwenCalls += 1;
        if (qwenCalls === 1) {
          return new Response(JSON.stringify({ choices: [{ message: {
            role: "assistant",
            content: null,
            tool_calls: [{ id: "diff-1", type: "function", function: { name: "diff_states", arguments: "{}" } }]
          } }] }), { status: 200 });
        }
        return new Response(JSON.stringify({ choices: [{ message: {
          role: "assistant",
          content: "Assessment: at 2026-09-29T12:01:00Z v1|ConfigMap|shop|api changed. Uncertainty: image evidence is incomplete."
        } }] }), { status: 200 });
      }
    });
    assert.equal(result.route, "qwen_investigation");
    assert.equal(result.qwen.toolCalls, 1);
    assert.equal(result.proposal.diagnosticAutomationExecuted, false);
    assert.equal(result.proposal.remediationExecuted, false);
    assert.ok(result.stageMetrics.qwen_input_guardrail >= 0);
    assert.ok(result.stageMetrics.qwen_output_guardrail >= 0);
    assert.equal(result.guardrails.qwenOutput.decision, "allow");
  } finally {
    store.close();
  }
});
