import crypto from "node:crypto";
import { performance } from "node:perf_hooks";
import { loadConfig } from "./config.mjs";
import { EmbeddingClient } from "./embedding.mjs";
import { logPipelineToMlflow, MlflowClient } from "./mlflow.mjs";
import { runIncidentPipeline } from "./pipeline.mjs";
import { TemporalStore } from "./store.mjs";

function usage() {
  return `usage: node src/pipeline-cli.mjs --baseline <RFC3339> --incident <RFC3339> [options]

Options:
  --namespace <name>     Namespace to investigate
  --scenario <name>      Non-sensitive scenario label
  --mlflow-url <url>     Internal MLflow tracking URL
  --help                 Show this help`;
}

function parseArguments(argv) {
  const options = {};
  const flags = new Map([
    ["--baseline", "baselineAt"],
    ["--incident", "incidentAt"],
    ["--namespace", "namespace"],
    ["--scenario", "scenario"],
    ["--mlflow-url", "mlflowUrl"]
  ]);
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === "--help" || flag === "-h") return { help: true };
    const key = flags.get(flag);
    if (!key) throw new Error(`Unknown option: ${flag}`);
    const value = argv[index + 1];
    if (value === undefined || value.startsWith("--")) throw new Error(`${flag} requires a value`);
    options[key] = value;
    index += 1;
  }
  return options;
}

function milliseconds(value) {
  return `${Number(value).toFixed(3)} ms`;
}

let store;
try {
  const options = parseArguments(process.argv.slice(2));
  if (options.help) {
    console.log(usage());
    process.exit(0);
  }
  if (!options.baselineAt || !options.incidentAt) throw new Error("--baseline and --incident are required");

  const config = loadConfig();
  store = new TemporalStore(config.dbPath);
  const embeddingClient = new EmbeddingClient({
    url: config.embeddingUrl,
    model: config.embeddingModel,
    apiKey: config.embeddingApiKey
  });
  const pipelineId = crypto.randomUUID();
  const scenario = options.scenario ?? "unspecified";
  const startedAt = new Date().toISOString();

  console.log(`\nKRAVEL LOCAL INCIDENT PIPELINE ${pipelineId.slice(0, 8)}`);
  console.log("=======================================");
  const result = await runIncidentPipeline({
    store,
    config,
    embeddingClient,
    baselineAt: options.baselineAt,
    incidentAt: options.incidentAt,
    namespace: options.namespace ?? "",
    scenario,
    onToolCall: ({ name }) => process.stderr.write(`[qwen] ${name}\n`)
  });

  const mlflow = new MlflowClient({ url: options.mlflowUrl ?? config.mlflowUrl });
  let mlflowStatus = "success";
  const mlflowStarted = performance.now();
  try {
    const logged = await logPipelineToMlflow(mlflow, { pipelineId, scenario, result });
    result.stageMetrics.mlflow_logging = logged.mlflowMs;
  } catch (error) {
    mlflowStatus = "unavailable";
    result.stageMetrics.mlflow_logging = performance.now() - mlflowStarted;
    console.error(`[mlflow] unavailable: ${error.message}`);
  }
  const totalMs = result.stageMetrics.pipeline_without_mlflow + result.stageMetrics.mlflow_logging;
  const finishedAt = new Date().toISOString();
  store.recordBenchmarkRun({
    comparisonId: pipelineId,
    clusterId: config.clusterId,
    scenario,
    flow: "incident_pipeline",
    provider: "local",
    model: result.qwen ? `${result.laya.model}+${result.qwen.model}` : result.laya.model,
    status: "success",
    startedAt,
    finishedAt,
    totalMs,
    evidenceMs: result.stageMetrics.evidence_reconstruction,
    modelMs: result.stageMetrics.laya_inference + (result.stageMetrics.qwen_inference ?? 0),
    toolMs: result.stageMetrics.qwen_temporal_tools ?? 0,
    toolCalls: result.qwen?.toolCalls ?? 0,
    confidence: result.laya.confidence,
    diagnosis: result.laya.diagnosis,
    route: result.route,
    decision: result.decision,
    reviewStatus: result.reviewStatus,
    stageMetrics: result.stageMetrics,
    errorCode: mlflowStatus === "success" ? "" : "mlflow_unavailable"
  });

  console.log(`Route: ${result.route}`);
  console.log(`Leading classification: ${result.decision} (${(result.policy.topProbability * 100).toFixed(1)}%)`);
  console.log(`Policy reasons: ${result.policy.reasons.join(", ")}`);
  console.log(`Human review: ${result.reviewStatus}`);
  console.log("\nStage latency:");
  for (const [stage, latency] of Object.entries(result.stageMetrics)) {
    console.log(`  ${stage.padEnd(28)} ${milliseconds(latency)}`);
  }
  console.log(`  ${"observed_total".padEnd(28)} ${milliseconds(totalMs)}`);

  console.log("\nGuardrail decisions:");
  console.log(`  Laya input:  ${result.guardrails.layaInput.decision}`);
  console.log(`  Laya output: ${result.guardrails.layaOutput.decision}`);
  console.log(`  Qwen input:  ${result.guardrails.qwenInput?.decision ?? "not_invoked"}`);
  console.log(`  Qwen output: ${result.guardrails.qwenOutput?.decision ?? "not_invoked"}`);

  if (result.qwen) {
    console.log("\nQwen evidence-grounded investigation:");
    console.log(result.qwen.report);
  }
  console.log("\nHuman-review remediation proposal (no mutation executed):");
  console.log(JSON.stringify(result.proposal, null, 2));
  console.log(`\nMLflow logging: ${mlflowStatus}. Prometheus will expose this run on Kravel's /metrics endpoint.`);
} catch (error) {
  console.error(`kravel pipeline failed: ${error.message}`);
  console.error(usage());
  process.exitCode = 1;
} finally {
  store?.close();
}
