import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { TemporalStore } from "../src/store.mjs";

function response(reply, status, payload) {
  const body = JSON.stringify(payload);
  reply.writeHead(status, { "content-type": "application/json", "content-length": Buffer.byteLength(body) });
  reply.end(body);
}

function runComparison(args, { env, input }) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, ["src/compare-cli.mjs", ...args], {
      cwd: process.cwd(),
      env: { ...process.env, ...env },
      stdio: ["pipe", "pipe", "pipe"]
    });
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8").on("data", (chunk) => { stdout += chunk; });
    child.stderr.setEncoding("utf8").on("data", (chunk) => { stderr += chunk; });
    child.once("error", reject);
    child.once("close", (code) => resolve({ code, stdout, stderr }));
    child.stdin.end(input);
  });
}

test("comparison CLI isolates both credentials and records both measured flows", async (context) => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "kravel-compare-"));
  const dbPath = path.join(directory, "compare.db");
  const store = new TemporalStore(dbPath);
  store.recordResourceChange({
    clusterId: "comparison-test",
    action: "ADDED",
    eventAt: "2026-09-28T12:00:00Z",
    object: { apiVersion: "v1", kind: "ConfigMap", metadata: { namespace: "shop", name: "api", resourceVersion: "1" }, data: { STARTUP_MODE: "healthy" } }
  });
  store.recordResourceChange({
    clusterId: "comparison-test",
    action: "MODIFIED",
    eventAt: "2026-09-28T12:01:00Z",
    object: { apiVersion: "v1", kind: "ConfigMap", metadata: { namespace: "shop", name: "api", resourceVersion: "2" }, data: { STARTUP_MODE: "broken" } }
  });
  store.close();

  let groqCalls = 0;
  let mlflowRuns = 0;
  const server = http.createServer(async (request, reply) => {
    const url = new URL(request.url, "http://localhost");
    if (url.pathname === "/groq/chat/completions") {
      groqCalls += 1;
      if (groqCalls === 1) {
        return response(reply, 200, { choices: [{ message: {
          role: "assistant",
          content: null,
          tool_calls: [{
            id: "diff-1",
            type: "function",
            function: { name: "diff_states", arguments: "{}" }
          }]
        } }] });
      }
      return response(reply, 200, { choices: [{ message: { role: "assistant", content: "Synthetic configuration regression report." } }] });
    }
    if (url.pathname === "/laya/v1/systemone") {
      return response(reply, 200, { answers: {
        config_regression: { noul: 0.96, answer_confidence: 0.96 },
        service_selector_drift: { noul: 0.1, answer_confidence: 0.9 },
        bad_image_rollout: { noul: 0.05, answer_confidence: 0.95 },
        scheduling_constraint: { noul: 0.04, answer_confidence: 0.96 }
      } });
    }
    if (url.pathname.endsWith("/experiments/get-by-name")) {
      return response(reply, 200, { experiment: { experiment_id: "1" } });
    }
    if (url.pathname.endsWith("/runs/create")) {
      mlflowRuns += 1;
      return response(reply, 200, { run: { info: { run_id: `run-${mlflowRuns}` } } });
    }
    if (url.pathname.endsWith("/runs/log-batch") || url.pathname.endsWith("/runs/update")) return response(reply, 200, {});
    return response(reply, 404, { error_code: "RESOURCE_DOES_NOT_EXIST" });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  context.after(async () => {
    await new Promise((resolve) => server.close(resolve));
    fs.rmSync(directory, { recursive: true, force: true });
  });
  const port = server.address().port;
  const layaUrl = `http://127.0.0.1:${port}/laya`;

  const result = await runComparison([
    "--secrets-stdin",
    "--baseline", "2026-09-28T12:00:30Z",
    "--incident", "2026-09-28T12:02:00Z",
    "--namespace", "shop",
    "--scenario", "synthetic",
    "--mlflow-url", `http://127.0.0.1:${port}/mlflow`
  ], {
    env: {
      KRAVEL_DB_PATH: dbPath,
      KRAVEL_CLUSTER_ID: "comparison-test",
      KRAVEL_LLM_BASE_URL: `http://127.0.0.1:${port}/groq`,
      KRAVEL_LLM_MODEL: "test-groq-model"
    },
    input: `test-groq-secret\ntest-laya-secret\n${layaUrl}\n`
  });

  assert.equal(result.code, 0, result.stderr);
  assert.match(result.stdout, /Groq agent:/);
  assert.match(result.stdout, /Laya classifier:/);
  assert.match(result.stdout, /MLflow: comparison metadata recorded/);
  assert.doesNotMatch(`${result.stdout}${result.stderr}`, /test-groq-secret|test-laya-secret|127\.0\.0\.1/);

  const recorded = new TemporalStore(dbPath);
  try {
    const runs = recorded.benchmarkRuns({ clusterId: "comparison-test" });
    assert.equal(runs.length, 2);
    assert.deepEqual(new Set(runs.map((run) => run.flow)), new Set(["groq_agent", "laya_classifier"]));
    assert.equal(runs.every((run) => run.status === "success"), true);
  } finally {
    recorded.close();
  }
});
