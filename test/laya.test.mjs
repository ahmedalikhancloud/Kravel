import assert from "node:assert/strict";
import test from "node:test";
import { buildClassifierEvidence, layaQuestions, runLayaClassifier } from "../src/laya.mjs";
import { TemporalStore } from "../src/store.mjs";

test("builds compact shared evidence and classifies it with Laya", async () => {
  const store = new TemporalStore(":memory:");
  try {
    store.recordResourceChange({
      clusterId: "demo",
      action: "ADDED",
      eventAt: "2026-09-28T12:00:00Z",
      object: { apiVersion: "v1", kind: "ConfigMap", metadata: { namespace: "shop", name: "api", resourceVersion: "1" }, data: { STARTUP_MODE: "healthy" } }
    });
    store.recordResourceChange({
      clusterId: "demo",
      action: "MODIFIED",
      eventAt: "2026-09-28T12:01:00Z",
      object: { apiVersion: "v1", kind: "ConfigMap", metadata: { namespace: "shop", name: "api", resourceVersion: "2" }, data: { STARTUP_MODE: "broken" } }
    });
    const evidence = buildClassifierEvidence({
      store,
      clusterId: "demo",
      baselineAt: "2026-09-28T12:00:30Z",
      incidentAt: "2026-09-28T12:02:00Z",
      namespace: "shop"
    });
    assert.match(evidence.state, /STARTUP_MODE/);
    assert.equal(evidence.changeCount, 1);
    assert.ok(evidence.state.length <= 1_600);

    let request;
    const result = await runLayaClassifier({
      url: "https://laya.invalid/v1/systemone/",
      apiKey: "runtime-only-secret",
      state: evidence.state,
      fetchImpl: async (url, options) => {
        request = { url: String(url), options, body: JSON.parse(options.body) };
        return new Response(JSON.stringify({
          answers: {
            config_regression: { type: "noul", noul: 0.94, answer_confidence: 0.94 },
            service_selector_drift: { type: "noul", noul: 0.11, answer_confidence: 0.89 },
            bad_image_rollout: { type: "noul", noul: 0.08, answer_confidence: 0.92 },
            scheduling_constraint: { type: "noul", noul: 0.05, answer_confidence: 0.95 }
          }
        }), { status: 200, headers: { "content-type": "application/json" } });
      }
    });
    assert.equal(request.url, "https://laya.invalid/v1/systemone");
    assert.equal(request.options.headers.authorization, "Bearer runtime-only-secret");
    assert.equal(request.body.questions.config_regression.type, "noul");
    assert.equal(result.diagnosis.config_regression, 0.94);
    assert.ok(result.modelMs >= 0);
  } finally {
    store.close();
  }
});

test("Laya noul questions include explicit criteria and neutral labels", () => {
  for (const question of Object.values(layaQuestions)) {
    assert.deepEqual(Object.keys(question.criteria).sort(), ["false", "true"]);
    assert.deepEqual(question.labels, { true: "A", false: "B" });
  }
});

test("refuses to send a Laya token over remote plain HTTP", async () => {
  await assert.rejects(
    runLayaClassifier({ url: "http://example.invalid", apiKey: "secret", state: "evidence" }),
    /Remote Laya must use HTTPS/
  );
});
