import assert from "node:assert/strict";
import test from "node:test";
import { runTemporalAgent } from "../src/agent.mjs";
import { TemporalStore } from "../src/store.mjs";

function configMap(mode, version) {
  return {
    apiVersion: "v1",
    kind: "ConfigMap",
    metadata: { namespace: "shop", name: "api-config", uid: "cm-1", resourceVersion: version },
    data: { STARTUP_MODE: mode }
  };
}

function modelResponse(message) {
  return new Response(JSON.stringify({ choices: [{ message }] }), {
    status: 200,
    headers: { "content-type": "application/json" }
  });
}

test("hosted agent executes temporal tool calls and returns the model report", async () => {
  const store = new TemporalStore(":memory:");
  const requests = [];
  try {
    store.recordResourceChange({
      clusterId: "test-cluster",
      action: "ADDED",
      eventAt: "2026-09-28T12:00:00Z",
      object: configMap("healthy", "1")
    });
    store.recordResourceChange({
      clusterId: "test-cluster",
      action: "MODIFIED",
      eventAt: "2026-09-28T12:04:00Z",
      object: configMap("broken", "2")
    });

    const fetchImpl = async (url, options) => {
      const body = JSON.parse(options.body);
      requests.push({ url: String(url), headers: options.headers, body });
      if (requests.length === 1) {
        return modelResponse({
          role: "assistant",
          content: null,
          tool_calls: [{
            id: "context-1",
            type: "function",
            function: {
              name: "get_incident_context",
              arguments: JSON.stringify({
                incident_at: "2026-09-28T12:05:00Z",
                lookback: "10m",
                namespace: "kube-system",
                cluster_id: "wrong-cluster"
              })
            }
          }]
        });
      }
      if (requests.length === 2) {
        return modelResponse({
          role: "assistant",
          content: null,
          tool_calls: [{
            id: "diff-1",
            type: "function",
            function: {
              name: "diff_states",
              arguments: JSON.stringify({
                from: "2026-09-28T12:01:00Z",
                to: "2026-09-28T12:05:00Z",
                namespace: "shop"
              })
            }
          }]
        });
      }
      return modelResponse({
        role: "assistant",
        content: "Assessment: ConfigMap v1|ConfigMap|shop|api-config changed at 2026-09-28T12:04:00. This is a leading candidate, not proven causality."
      });
    };

    const observedTools = [];
    const result = await runTemporalAgent({
      store,
      config: {
        clusterId: "test-cluster",
        llmApiKey: "",
        llmBaseUrl: "https://llm.example/v1",
        llmModel: "test-model",
        llmMaxTurns: 4
      },
      embeddingClient: { enabled: false },
      incidentAt: "2026-09-28T12:05:00Z",
      baselineAt: "2026-09-28T12:01:00Z",
      namespace: "shop",
      apiKey: "test-secret",
      fetchImpl,
      onToolCall: ({ name }) => observedTools.push(name)
    });

    assert.equal(result.model, "test-model");
    assert.equal(result.toolCalls, 2);
    assert.match(result.answer, /leading candidate, not proven causality/);
    assert.deepEqual(observedTools, ["get_incident_context", "diff_states"]);
    assert.equal(requests[0].url, "https://llm.example/v1/chat/completions");
    assert.equal(requests[0].headers.authorization, "Bearer test-secret");
    assert.equal(requests[0].body.tool_choice, "required");
    assert.equal(requests[0].body.tools.length, 4);
    assert.equal(requests[0].body.tools[0].function.parameters.properties.namespace, undefined);
    assert.equal(requests[0].body.tools[0].function.parameters.properties.cluster_id, undefined);
    assert.match(requests[1].body.messages.at(-1).content, /"clusterId":"test-cluster"/);
    assert.match(requests[1].body.messages.at(-1).content, /"namespace":"shop"/);
    assert.match(requests[1].body.messages.at(-1).content, /STARTUP_MODE/);
    assert.match(requests[2].body.messages.at(-1).content, /\/data\/STARTUP_MODE/);
  } finally {
    store.close();
  }
});

test("hosted agent refuses to run without an API key", async () => {
  const store = new TemporalStore(":memory:");
  try {
    await assert.rejects(
      runTemporalAgent({
        store,
        config: { clusterId: "test", llmApiKey: "", llmBaseUrl: "", llmModel: "" },
        embeddingClient: { enabled: false },
        incidentAt: "2026-09-28T12:05:00Z",
        apiKey: ""
      }),
      /No LLM API key supplied/
    );
  } finally {
    store.close();
  }
});
