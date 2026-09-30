import assert from "node:assert/strict";
import { PassThrough } from "node:stream";
import test from "node:test";
import { KubernetesWatcher } from "../src/kube-watcher.mjs";

function watcherWithStore(changes) {
  return new KubernetesWatcher({
    kube: { host: "kubernetes.default.svc", port: 443 },
    resources: ["/api/v1/configmaps"],
    clusterId: "test-cluster",
    store: {
      recordResourceChange(change) { changes.push(change); },
      recordKubernetesEvent() {}
    },
    onError() {},
    onInfo() {}
  });
}

test("watcher inherits TypeMeta omitted from Kubernetes list items", async () => {
  const changes = [];
  const watcher = watcherWithStore(changes);
  watcher.request = async () => ({
    apiVersion: "v1",
    kind: "ConfigMapList",
    metadata: { resourceVersion: "10" },
    items: [{ metadata: { namespace: "shop", name: "api", resourceVersion: "9" }, data: { mode: "healthy" } }]
  });

  const resourceVersion = await watcher.list("/api/v1/configmaps");

  assert.equal(resourceVersion, "10");
  assert.equal(changes.length, 1);
  assert.equal(changes[0].object.apiVersion, "v1");
  assert.equal(changes[0].object.kind, "ConfigMap");
  assert.equal(changes[0].object.metadata.name, "api");
});

test("watcher applies remembered TypeMeta to watch events", async () => {
  const changes = [];
  const watcher = watcherWithStore(changes);
  watcher.request = async (_path, { stream } = {}) => {
    if (!stream) {
      return { apiVersion: "v1", kind: "ConfigMapList", metadata: { resourceVersion: "10" }, items: [] };
    }
    const response = new PassThrough();
    queueMicrotask(() => response.end(`${JSON.stringify({
      type: "MODIFIED",
      object: { metadata: { namespace: "shop", name: "api", resourceVersion: "11" }, data: { mode: "broken" } }
    })}\n`));
    return response;
  };

  await watcher.list("/api/v1/configmaps");
  const resourceVersion = await watcher.watch("/api/v1/configmaps", "10");

  assert.equal(resourceVersion, "11");
  assert.equal(changes.length, 1);
  assert.equal(changes[0].action, "MODIFIED");
  assert.equal(changes[0].object.apiVersion, "v1");
  assert.equal(changes[0].object.kind, "ConfigMap");
});
