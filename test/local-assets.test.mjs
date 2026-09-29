import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

test("local deployment uses internal model endpoints and read-only Kubernetes access", () => {
  const manifest = fs.readFileSync("deploy/local.yaml", "utf8");
  assert.match(manifest, /image: kravel:local/);
  assert.match(manifest, /imagePullPolicy: Never/);
  assert.match(manifest, /model-runner\.docker\.internal\/engines\/v1/);
  assert.match(manifest, /kravel-laya\.kravel-ai\.svc\.cluster\.local/);
  assert.match(manifest, /verbs: \["get", "list", "watch"\]/);
  assert.doesNotMatch(manifest, /apiVersion: v1\s+kind: Secret/);
});

test("Laya is CPU-only, private to the cluster, and uses a persistent model cache", () => {
  const manifest = fs.readFileSync("deploy/laya-local.yaml", "utf8");
  assert.match(manifest, /value: cpu/);
  assert.match(manifest, /kind: PersistentVolumeClaim/);
  assert.match(manifest, /type: ClusterIP/);
  assert.doesNotMatch(manifest, /NodePort|LoadBalancer/);
  const dockerfile = fs.readFileSync("demo/local/laya.Dockerfile", "utf8");
  assert.match(dockerfile, /laya\[serve\]==0\.3\.21/);
});

test("observability dashboard exposes model, guardrail, policy, and MLflow stage timing without secrets", () => {
  const manifest = fs.readFileSync("deploy/observability-local.yaml", "utf8");
  assert.match(manifest, /kravel_pipeline_stage_latency_seconds/);
  for (const stage of ["laya_input_guardrail", "laya_inference", "qwen_input_guardrail", "qwen_inference", "predefined_automation", "mlflow_logging"]) {
    assert.match(manifest, new RegExp(stage));
  }
  assert.match(manifest, /storage\.tsdb\.retention\.time=1h/);
  assert.doesNotMatch(manifest, /kind:\s+Secret/);
  assert.doesNotMatch(manifest, /(api[_-]?key|token|password):\s*[^\s{}]/i);

  const dashboardBlock = /dashboard\.json: \|\r?\n([\s\S]*?)\r?\n---/.exec(manifest)?.[1];
  assert.ok(dashboardBlock);
  const dashboard = JSON.parse(dashboardBlock.replace(/^    /gm, ""));
  assert.equal(dashboard.uid, "kravel-local-pipeline");
  assert.ok(dashboard.panels.length >= 8);
});

test("local scripts bind browser ports to loopback and contain no committed credentials", () => {
  const scripts = fs.readdirSync("demo/local")
    .filter((name) => name.endsWith(".ps1"))
    .map((name) => fs.readFileSync(`demo/local/${name}`, "utf8"))
    .join("\n");
  assert.match(scripts, /--address=127\.0\.0\.1/);
  assert.match(scripts, /docker-desktop/);
  assert.doesNotMatch(scripts, /gsk_[A-Za-z0-9_-]{16,}|BEGIN PRIVATE KEY/i);
});
