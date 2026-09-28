import readline from "node:readline";
import { parseDurationSeconds } from "./time.mjs";

export const temporalTools = [
  {
    name: "rewind_cluster_state",
    description: "Reconstruct the exact last-observed Kubernetes object state at a timestamp.",
    inputSchema: {
      type: "object",
      required: ["timestamp"],
      properties: {
        timestamp: { type: "string", description: "RFC 3339 timestamp" },
        cluster_id: { type: "string" },
        namespace: { type: "string" },
        kinds: { type: "array", items: { type: "string" } },
        resource_key: { type: "string" }
      }
    }
  },
  {
    name: "diff_states",
    description: "Return deterministic Kubernetes object changes between two timestamps.",
    inputSchema: {
      type: "object",
      required: ["from", "to"],
      properties: {
        from: { type: "string" },
        to: { type: "string" },
        cluster_id: { type: "string" },
        namespace: { type: "string" },
        kinds: { type: "array", items: { type: "string" } }
      }
    }
  },
  {
    name: "get_incident_context",
    description: "Build a time-bounded evidence shard from state changes, audit actors, Kubernetes events, and metrics. Relevance is not proof of causality.",
    inputSchema: {
      type: "object",
      required: ["incident_at"],
      properties: {
        incident_at: { type: "string" },
        lookback: { type: ["string", "number"], description: "For example 15m or 900" },
        cluster_id: { type: "string" },
        namespace: { type: "string" },
        resource_key: { type: "string" },
        question: { type: "string" },
        limit: { type: "integer", minimum: 1, maximum: 500 }
      }
    }
  },
  {
    name: "trace_resource",
    description: "Trace ownership, selectors, ConfigMap, Secret, volume, and service-account relationships at a timestamp.",
    inputSchema: {
      type: "object",
      required: ["timestamp", "resource_key"],
      properties: {
        timestamp: { type: "string" },
        resource_key: { type: "string" },
        cluster_id: { type: "string" },
        max_depth: { type: "integer", minimum: 0, maximum: 8 }
      }
    }
  }
];

export async function executeTemporalTool({ name, args = {}, store, config, embeddingClient }) {
  const clusterId = args.cluster_id ?? config.clusterId;
  switch (name) {
    case "rewind_cluster_state":
      return store.stateAt({
        clusterId,
        timestamp: args.timestamp,
        namespace: args.namespace,
        kinds: args.kinds,
        resourceKey: args.resource_key
      });
    case "diff_states":
      return store.diffStates({
        clusterId,
        from: args.from,
        to: args.to,
        namespace: args.namespace,
        kinds: args.kinds
      });
    case "get_incident_context": {
      const queryEmbedding = args.question && embeddingClient?.enabled
        ? await embeddingClient.embed(args.question)
        : null;
      return store.contextShard({
        clusterId,
        incidentAt: args.incident_at,
        lookback: parseDurationSeconds(args.lookback, 900),
        namespace: args.namespace,
        resourceKey: args.resource_key,
        queryEmbedding,
        limit: args.limit
      });
    }
    case "trace_resource":
      return store.traceResource({
        clusterId,
        timestamp: args.timestamp,
        resourceKey: args.resource_key,
        maxDepth: args.max_depth
      });
    default:
      throw new Error(`Unknown tool: ${name}`);
  }
}

const modernProtocolVersion = "2026-07-28";
const legacyProtocolVersion = "2025-11-25";
const serverInfo = { name: "kravel", version: "0.1.0" };

function isModern(message) {
  return message.method === "server/discover"
    || message.params?._meta?.["io.modelcontextprotocol/protocolVersion"] === modernProtocolVersion;
}

function decorateResult(result, modern, { cacheable = false } = {}) {
  if (!modern) return result;
  return {
    resultType: "complete",
    ...result,
    ...(cacheable ? { ttlMs: 0, cacheScope: "private" } : {}),
    _meta: {
      ...(result._meta ?? {}),
      "io.modelcontextprotocol/serverInfo": serverInfo
    }
  };
}

function content(value) {
  return { content: [{ type: "text", text: JSON.stringify(value, null, 2) }] };
}

export async function runMcpServer({ store, config, embeddingClient, input = process.stdin, output = process.stdout }) {
  const lines = readline.createInterface({ input, crlfDelay: Infinity });
  const reply = (id, value) => output.write(`${JSON.stringify({ jsonrpc: "2.0", id, ...value })}\n`);
  for await (const line of lines) {
    if (!line.trim()) continue;
    let message;
    try {
      message = JSON.parse(line);
      const requestedVersion = message.params?._meta?.["io.modelcontextprotocol/protocolVersion"];
      if (requestedVersion && requestedVersion !== modernProtocolVersion) {
        reply(message.id, { error: { code: -32022, message: "Unsupported protocol version", data: { supported: [modernProtocolVersion], requested: requestedVersion } } });
      } else if (message.method === "server/discover") {
        reply(message.id, { result: decorateResult({
          supportedVersions: [modernProtocolVersion],
          capabilities: { tools: {} },
          instructions: "Use rewind_cluster_state and diff_states for deterministic state. Use get_incident_context only to retrieve evidence; relevance does not prove causality."
        }, true, { cacheable: true }) });
      } else if (message.method === "initialize") {
        reply(message.id, { result: { protocolVersion: legacyProtocolVersion, capabilities: { tools: {} }, serverInfo } });
      } else if (message.method === "tools/list") {
        const modern = isModern(message);
        reply(message.id, { result: decorateResult({ tools: temporalTools }, modern, { cacheable: true }) });
      } else if (message.method === "tools/call") {
        const modern = isModern(message);
        const args = message.params?.arguments ?? {};
        const result = await executeTemporalTool({
          name: message.params?.name,
          args,
          store,
          config,
          embeddingClient
        });
        reply(message.id, { result: decorateResult(content(result), modern) });
      } else if (message.method === "ping") {
        reply(message.id, { result: decorateResult({}, isModern(message)) });
      } else if (message.id !== undefined && !message.method?.startsWith("notifications/")) {
        reply(message.id, { error: { code: -32601, message: "Method not found" } });
      }
    } catch (error) {
      if (message?.id !== undefined) reply(message.id, { error: { code: -32603, message: error.message } });
    }
  }
}
