import assert from "node:assert/strict";
import test from "node:test";
import { createApiServer, listen } from "../src/api.mjs";
import { EmbeddingClient } from "../src/embedding.mjs";
import { TemporalStore } from "../src/store.mjs";

test("HTTP API ingests and rewinds a resource", async (context) => {
  const store = new TemporalStore(":memory:");
  const config = { clusterId: "api-test", apiToken: "test-token", maxBodyBytes: 1024 * 1024, retentionDays: 30 };
  const server = createApiServer({ store, config, embeddingClient: new EmbeddingClient() });
  const address = await listen(server, "127.0.0.1", 0);
  context.after(async () => {
    await new Promise((resolve) => server.close(resolve));
    store.close();
  });
  const baseUrl = `http://127.0.0.1:${address.port}`;

  const unauthorized = await fetch(`${baseUrl}/v1/state/rewind?timestamp=2026-09-28T12:01:00Z`);
  assert.equal(unauthorized.status, 401);

  const ingested = await fetch(`${baseUrl}/v1/ingest/resource`, {
    method: "POST",
    headers: { authorization: "Bearer test-token", "content-type": "application/json" },
    body: JSON.stringify({
      action: "ADDED",
      eventAt: "2026-09-28T12:00:00Z",
      object: { apiVersion: "v1", kind: "ConfigMap", metadata: { namespace: "shop", name: "api", resourceVersion: "1" }, data: { timeout: "30s" } }
    })
  });
  assert.equal(ingested.status, 201);

  const rewound = await fetch(`${baseUrl}/v1/state/rewind?timestamp=2026-09-28T12:01:00Z`, {
    headers: { authorization: "Bearer test-token" }
  });
  const payload = await rewound.json();
  assert.equal(payload.objectCount, 1);
  assert.equal(payload.objects[0].data.timeout, "30s");
});
