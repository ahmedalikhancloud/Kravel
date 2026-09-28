import crypto from "node:crypto";
import { performance } from "node:perf_hooks";
import { runTemporalAgent } from "./agent.mjs";
import { loadConfig } from "./config.mjs";
import { EmbeddingClient } from "./embedding.mjs";
import { buildClassifierEvidence, runLayaClassifier } from "./laya.mjs";
import { logComparisonToMlflow, MlflowClient } from "./mlflow.mjs";
import { TemporalStore } from "./store.mjs";

const defaultQuestion = "Investigate the complete incident window. Identify every independent failure with absolute timestamps and resource keys. Do not stop after the first cause.";

function usage() {
  return `usage: node src/compare-cli.mjs --baseline <RFC3339> --incident <RFC3339> --secrets-stdin [options]

Standard input contains exactly three lines: Groq key, optional Laya token, and Laya HTTPS URL.

Options:
  --namespace <name>     Namespace to investigate
  --scenario <name>      Non-sensitive scenario label
  --groq-model <name>    Groq model override
  --laya-model <name>    Laya checkpoint name
  --question <text>      Groq investigation question
  --mlflow-url <url>     Internal MLflow tracking URL
  --secrets-stdin        Read runtime credentials and Laya URL from stdin
  --help                 Show this help`;
}

function parseArguments(argv) {
  const options = {};
  const flags = new Map([
    ["--baseline", "baselineAt"],
    ["--incident", "incidentAt"],
    ["--namespace", "namespace"],
    ["--scenario", "scenario"],
    ["--groq-model", "groqModel"],
    ["--laya-model", "layaModel"],
    ["--question", "question"],
    ["--mlflow-url", "mlflowUrl"]
  ]);
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === "--help" || flag === "-h") return { help: true };
    if (flag === "--secrets-stdin") {
      options.secretsStdin = true;
      continue;
    }
    const key = flags.get(flag);
    if (!key) throw new Error(`Unknown option: ${flag}`);
    const value = argv[index + 1];
    if (value === undefined || value.startsWith("--")) throw new Error(`${flag} requires a value`);
    options[key] = value;
    index += 1;
  }
  return options;
}

async function readRuntimeInput(maxBytes = 65_536) {
  const chunks = [];
  let size = 0;
  for await (const chunk of process.stdin) {
    size += chunk.length;
    if (size > maxBytes) throw new Error("runtime input is unexpectedly large");
    chunks.push(chunk);
  }
  const lines = Buffer.concat(chunks).toString("utf8").replace(/\r/g, "").split("\n");
  return { groqApiKey: lines[0]?.trim() ?? "", layaApiKey: lines[1]?.trim() ?? "", layaUrl: lines[2]?.trim() ?? "" };
}

function errorCode(error) {
  if (error?.statusCode) return `http_${error.statusCode}`;
  if (/timed out/i.test(error?.message)) return "timeout";
  if (/API key/i.test(error?.message)) return "missing_key";
  return "unavailable";
}

function rounded(value) {
  return Number.isFinite(Number(value)) ? Number(Number(value).toFixed(3)) : null;
}

function createRun(fields) {
  return {
    scenario: fields.scenario,
    flow: fields.flow,
    provider: fields.provider,
    model: fields.model,
    status: fields.status,
    startedAt: fields.startedAt,
    finishedAt: fields.finishedAt,
    totalMs: rounded(fields.totalMs),
    evidenceMs: rounded(fields.evidenceMs),
    modelMs: rounded(fields.modelMs),
    toolMs: rounded(fields.toolMs),
    toolCalls: fields.toolCalls ?? 0,
    confidence: rounded(fields.confidence),
    diagnosis: fields.diagnosis ?? {},
    errorCode: fields.errorCode ?? ""
  };
}

let store;
try {
  const options = parseArguments(process.argv.slice(2));
  if (options.help) {
    console.log(usage());
    process.exit(0);
  }
  if (!options.baselineAt || !options.incidentAt) throw new Error("--baseline and --incident are required");
  if (!options.secretsStdin) throw new Error("--secrets-stdin is required so credentials do not appear in process arguments");
  const runtime = await readRuntimeInput();
  if (!runtime.groqApiKey) throw new Error("Groq API key is required");
  if (!runtime.layaUrl) throw new Error("Laya HTTPS URL is required");

  const config = loadConfig();
  store = new TemporalStore(config.dbPath);
  const embeddingClient = new EmbeddingClient({ url: config.embeddingUrl, model: config.embeddingModel, apiKey: config.embeddingApiKey });
  const comparisonId = crypto.randomUUID();
  const scenario = options.scenario ?? "unspecified";
  const namespace = options.namespace ?? "";
  const runs = [];

  console.log(`\nKRAVEL FLOW COMPARISON ${comparisonId.slice(0, 8)}`);
  console.log("================================");

  const groqStartedAt = new Date().toISOString();
  const groqStarted = performance.now();
  try {
    const result = await runTemporalAgent({
      store,
      config,
      embeddingClient,
      incidentAt: options.incidentAt,
      baselineAt: options.baselineAt,
      namespace,
      question: options.question ?? defaultQuestion,
      apiKey: runtime.groqApiKey,
      model: options.groqModel,
      onToolCall: ({ name }) => process.stderr.write(`[groq] ${name}\n`)
    });
    const run = createRun({
      scenario,
      flow: "groq_agent",
      provider: "groq",
      model: result.model,
      status: "success",
      startedAt: groqStartedAt,
      finishedAt: new Date().toISOString(),
      totalMs: performance.now() - groqStarted,
      modelMs: result.modelMs,
      toolMs: result.toolMs,
      toolCalls: result.toolCalls
    });
    runs.push(run);
    console.log(`\nGroq agent: ${run.totalMs.toFixed(1)} ms total; ${run.modelMs.toFixed(1)} ms hosted-model time; ${run.toolCalls} tool calls`);
    console.log(result.answer);
  } catch (error) {
    runs.push(createRun({
      scenario,
      flow: "groq_agent",
      provider: "groq",
      model: options.groqModel ?? config.llmModel,
      status: "failed",
      startedAt: groqStartedAt,
      finishedAt: new Date().toISOString(),
      totalMs: performance.now() - groqStarted,
      errorCode: errorCode(error)
    }));
    console.error(`\nGroq agent unavailable (${errorCode(error)}); continuing with Laya.`);
  }

  const layaStartedAt = new Date().toISOString();
  const layaStarted = performance.now();
  try {
    const evidence = buildClassifierEvidence({
      store,
      clusterId: config.clusterId,
      baselineAt: options.baselineAt,
      incidentAt: options.incidentAt,
      namespace
    });
    const result = await runLayaClassifier({
      url: runtime.layaUrl,
      apiKey: runtime.layaApiKey,
      model: options.layaModel ?? "english",
      state: evidence.state
    });
    const run = createRun({
      scenario,
      flow: "laya_classifier",
      provider: "laya",
      model: result.model,
      status: "success",
      startedAt: layaStartedAt,
      finishedAt: new Date().toISOString(),
      totalMs: performance.now() - layaStarted,
      evidenceMs: evidence.evidenceMs,
      modelMs: result.modelMs,
      confidence: result.confidence,
      diagnosis: result.diagnosis
    });
    runs.push(run);
    console.log(`\nLaya classifier: ${run.totalMs.toFixed(1)} ms total; ${run.evidenceMs.toFixed(1)} ms evidence; ${run.modelMs.toFixed(1)} ms model round trip`);
    for (const [name, probability] of Object.entries(result.diagnosis)) {
      console.log(`  ${name}: ${(probability * 100).toFixed(1)}%`);
    }
  } catch (error) {
    runs.push(createRun({
      scenario,
      flow: "laya_classifier",
      provider: "laya",
      model: options.layaModel ?? "english",
      status: "unavailable",
      startedAt: layaStartedAt,
      finishedAt: new Date().toISOString(),
      totalMs: performance.now() - layaStarted,
      errorCode: errorCode(error)
    }));
    console.error(`\nLaya classifier unavailable (${errorCode(error)}); the Groq result and dashboard remain usable.`);
  }

  for (const run of runs) store.recordBenchmarkRun({ comparisonId, clusterId: config.clusterId, ...run });

  const mlflow = new MlflowClient({
    url: options.mlflowUrl ?? process.env.KRAVEL_MLFLOW_URL ?? "http://kravel-mlflow.kravel-observability.svc.cluster.local:5000"
  });
  try {
    await logComparisonToMlflow(mlflow, { comparisonId, scenario, runs });
    console.log("\nMLflow: comparison metadata recorded (no prompts, answers, credentials, or endpoint URLs)." );
  } catch (error) {
    console.error(`\nMLflow unavailable (${errorCode(error)}); Prometheus metrics were still recorded.`);
  }
  console.log("Grafana: refresh the Kravel Flow Comparison dashboard after Prometheus's next scrape.");
} catch (error) {
  console.error(`kravel comparison failed: ${error.message}`);
  console.error(usage());
  process.exitCode = 1;
} finally {
  store?.close();
}
