import { createApiServer, listen } from "./api.mjs";
import { loadConfig } from "./config.mjs";
import { EmbeddingClient, EmbeddingWorker } from "./embedding.mjs";
import { KubernetesWatcher } from "./kube-watcher.mjs";
import { runMcpServer } from "./mcp.mjs";
import { PrometheusSampler } from "./prometheus.mjs";
import { TemporalStore } from "./store.mjs";

const command = process.argv[2] ?? "serve";
const config = loadConfig();
const store = new TemporalStore(config.dbPath);
const embeddingClient = new EmbeddingClient({
  url: config.embeddingUrl,
  model: config.embeddingModel,
  apiKey: config.embeddingApiKey
});
const embeddingWorker = new EmbeddingWorker({ client: embeddingClient, store });
const watcher = new KubernetesWatcher({
  kube: config.kube,
  resources: config.watchResources,
  clusterId: config.clusterId,
  store
});
const prometheus = new PrometheusSampler({
  url: config.prometheusUrl,
  intervalMs: config.prometheusIntervalMs,
  queries: config.prometheusQueries,
  clusterId: config.clusterId,
  store
});

let server;
let closing = false;
async function shutdown(signal) {
  if (closing) return;
  closing = true;
  console.error(`[kravel] received ${signal}, stopping`);
  watcher.stop();
  prometheus.stop();
  embeddingWorker.stop();
  if (server) await new Promise((resolve) => server.close(resolve));
  store.close();
}

process.on("SIGINT", () => shutdown("SIGINT").finally(() => process.exit(0)));
process.on("SIGTERM", () => shutdown("SIGTERM").finally(() => process.exit(0)));

try {
  if (command === "mcp") {
    await runMcpServer({ store, config, embeddingClient });
    store.close();
  } else if (["serve", "serve-watch"].includes(command)) {
    server = createApiServer({ store, config, embeddingClient });
    const address = await listen(server, config.host, config.port);
    console.log(`[kravel] API listening on ${typeof address === "string" ? address : `${address.address}:${address.port}`}`);
    void embeddingWorker.start();
    void prometheus.start();
    if (command === "serve-watch") void watcher.start().catch((error) => console.error(`[watch] stopped: ${error.stack ?? error.message}`));
  } else if (command === "watch") {
    void embeddingWorker.start();
    void prometheus.start();
    await watcher.start();
  } else {
    throw new Error(`Unknown command: ${command}`);
  }
} catch (error) {
  console.error(error.stack ?? error.message);
  store.close();
  process.exitCode = 1;
}
