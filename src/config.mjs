import os from "node:os";
import path from "node:path";

function integer(name, fallback) {
  const value = process.env[name];
  if (value === undefined || value === "") return fallback;
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) throw new Error(`${name} must be an integer`);
  return parsed;
}

function json(name, fallback) {
  const value = process.env[name];
  if (!value) return fallback;
  try {
    return JSON.parse(value);
  } catch (error) {
    throw new Error(`${name} must contain valid JSON: ${error.message}`);
  }
}

export function loadConfig() {
  return {
    host: process.env.KRAVEL_HOST ?? "127.0.0.1",
    port: integer("KRAVEL_PORT", 8080),
    dbPath: process.env.KRAVEL_DB_PATH ?? path.join(process.cwd(), "data", "kravel.db"),
    clusterId: process.env.KRAVEL_CLUSTER_ID ?? os.hostname(),
    retentionDays: integer("KRAVEL_RETENTION_DAYS", 30),
    maxBodyBytes: integer("KRAVEL_MAX_BODY_BYTES", 8 * 1024 * 1024),
    apiToken: process.env.KRAVEL_API_TOKEN ?? "",
    watchResources: json("KRAVEL_WATCH_RESOURCES", [
      "/api/v1/pods",
      "/api/v1/configmaps",
      "/api/v1/services",
      "/api/v1/serviceaccounts",
      "/api/v1/persistentvolumeclaims",
      "/api/v1/namespaces",
      "/apis/apps/v1/deployments",
      "/apis/apps/v1/statefulsets",
      "/apis/apps/v1/daemonsets",
      "/apis/apps/v1/replicasets",
      "/apis/batch/v1/jobs",
      "/apis/batch/v1/cronjobs",
      "/apis/networking.k8s.io/v1/ingresses",
      "/apis/events.k8s.io/v1/events"
    ]),
    prometheusUrl: process.env.KRAVEL_PROMETHEUS_URL ?? "",
    prometheusIntervalMs: integer("KRAVEL_PROMETHEUS_INTERVAL_MS", 30_000),
    prometheusQueries: json("KRAVEL_PROMETHEUS_QUERIES", {
      pod_restarts: "sum by (namespace, pod) (kube_pod_container_status_restarts_total)",
      pod_not_ready: "sum by (namespace, pod) (kube_pod_status_ready{condition=\"false\"})",
      cpu_throttling: "sum by (namespace, pod) (rate(container_cpu_cfs_throttled_periods_total[5m]))"
    }),
    embeddingUrl: process.env.KRAVEL_EMBEDDING_URL ?? "",
    embeddingModel: process.env.KRAVEL_EMBEDDING_MODEL ?? "",
    embeddingApiKey: process.env.KRAVEL_EMBEDDING_API_KEY ?? "",
    llmBaseUrl: process.env.KRAVEL_LLM_BASE_URL ?? "https://api.groq.com/openai/v1",
    llmModel: process.env.KRAVEL_LLM_MODEL ?? "openai/gpt-oss-20b",
    llmApiKey: process.env.KRAVEL_LLM_API_KEY ?? process.env.GROQ_API_KEY ?? "",
    llmMaxTurns: integer("KRAVEL_LLM_MAX_TURNS", 6),
    kube: {
      host: process.env.KUBERNETES_SERVICE_HOST ?? "",
      port: integer("KUBERNETES_SERVICE_PORT_HTTPS", 443),
      tokenPath: process.env.KRAVEL_KUBE_TOKEN_PATH ?? "/var/run/secrets/kubernetes.io/serviceaccount/token",
      caPath: process.env.KRAVEL_KUBE_CA_PATH ?? "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    }
  };
}
