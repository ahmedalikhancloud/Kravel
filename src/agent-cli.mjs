import { runTemporalAgent } from "./agent.mjs";
import { loadConfig } from "./config.mjs";
import { EmbeddingClient } from "./embedding.mjs";
import { TemporalStore } from "./store.mjs";

function usage() {
  return `usage: node src/agent-cli.mjs --incident <RFC3339> [options]

Options:
  --baseline <RFC3339>    Known-good timestamp
  --namespace <name>      Namespace to investigate
  --question <text>       Investigation question
  --model <name>          Hosted model name
  --base-url <url>        OpenAI-compatible API base URL
  --max-turns <number>    Maximum tool-calling turns (1-10)
  --api-key-stdin         Read the API key from standard input
  --help                  Show this help`;
}

function parseArguments(argv) {
  const options = {};
  const valueFlags = new Map([
    ["--incident", "incidentAt"],
    ["--baseline", "baselineAt"],
    ["--namespace", "namespace"],
    ["--question", "question"],
    ["--model", "model"],
    ["--base-url", "baseUrl"],
    ["--max-turns", "maxTurns"]
  ]);
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (flag === "--help" || flag === "-h") return { help: true };
    if (flag === "--api-key-stdin") {
      options.apiKeyStdin = true;
      continue;
    }
    const key = valueFlags.get(flag);
    if (!key) throw new Error(`Unknown option: ${flag}`);
    const value = argv[index + 1];
    if (value === undefined || value.startsWith("--")) throw new Error(`${flag} requires a value`);
    options[key] = value;
    index += 1;
  }
  return options;
}

async function readSecretFromStdin(maxBytes = 16_384) {
  const chunks = [];
  let size = 0;
  for await (const chunk of process.stdin) {
    size += chunk.length;
    if (size > maxBytes) throw new Error("API key input is unexpectedly large");
    chunks.push(chunk);
  }
  return Buffer.concat(chunks).toString("utf8").trim();
}

let store;
try {
  const options = parseArguments(process.argv.slice(2));
  if (options.help) {
    console.log(usage());
    process.exit(0);
  }
  if (!options.incidentAt) throw new Error("--incident is required");

  const config = loadConfig();
  const apiKey = options.apiKeyStdin ? await readSecretFromStdin() : config.llmApiKey;
  store = new TemporalStore(config.dbPath);
  const embeddingClient = new EmbeddingClient({
    url: config.embeddingUrl,
    model: config.embeddingModel,
    apiKey: config.embeddingApiKey
  });
  const result = await runTemporalAgent({
    store,
    config,
    embeddingClient,
    incidentAt: options.incidentAt,
    baselineAt: options.baselineAt,
    namespace: options.namespace,
    question: options.question,
    apiKey,
    baseUrl: options.baseUrl,
    model: options.model,
    maxTurns: options.maxTurns,
    onToolCall: ({ name, args }) => {
      const scope = args.resource_key || args.namespace || "cluster";
      process.stderr.write(`[kravel-agent] ${name} (${scope})\n`);
    }
  });

  console.log("\nKRAVEL HOSTED-LLM INCIDENT REPORT");
  console.log("=================================");
  console.log(`Model: ${result.model}`);
  console.log(`Temporal tool calls: ${result.toolCalls}`);
  console.log("");
  console.log(result.answer);
} catch (error) {
  console.error(`kravel agent failed: ${error.message}`);
  console.error(usage());
  process.exitCode = 1;
} finally {
  store?.close();
}
