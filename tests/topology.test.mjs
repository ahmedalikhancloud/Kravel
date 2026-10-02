import test from "node:test";
import assert from "node:assert/strict";
import { resourceId, layoutTopology, matchingResources } from "../kravel/web/topology.mjs";

const resources = ["Deployment/api", "ReplicaSet/api-v1", "Pod/api-pod", "ConfigMap/config", "Service/gateway"].map((value) => {
  const [kind, name] = value.split("/"); return {kind, name, namespace: "demo"};
});
const edge = (source, target, relation) => ({source: `demo/${source}`, target: `demo/${target}`, relation});
const connections = [edge("Deployment/api", "ReplicaSet/api-v1", "owns"), edge("ReplicaSet/api-v1", "Pod/api-pod", "owns"), edge("ConfigMap/config", "Pod/api-pod", "configures"), edge("Service/gateway", "Pod/api-pod", "selects")];

test("DaemonSets and StatefulSets are controller roots with selectable owned Pods", () => {
  const items = ["DaemonSet/logs", "Pod/log-node", "StatefulSet/db", "Pod/db-0"].map((value) => { const [kind, name] = value.split("/"); return {kind, name, namespace: "demo"}; });
  const links = [edge("DaemonSet/logs", "Pod/log-node", "owns"), edge("StatefulSet/db", "Pod/db-0", "owns")];
  const layout = layoutTopology(items, links);
  assert.equal(layout.get("demo/Pod/log-node").group, "demo/DaemonSet/logs");
  assert.equal(layout.get("demo/Pod/db-0").group, "demo/StatefulSet/db");
  assert.equal(matchingResources(items, "", "controllers").length, 2);
});

test("five kinds share the actual controller root and occupy separate lanes", () => {
  const layout = layoutTopology(resources, connections);
  assert.equal(layout.size, 5);
  assert.equal(new Set([...layout.values()].map((value) => value.group)).size, 1);
  assert.equal(new Set([...layout.values()].map((value) => value.z)).size, 5);
});
test("polling order does not move resources around", () => {
  assert.deepEqual(layoutTopology(resources, connections), layoutTopology([...resources].reverse(), [...connections].reverse()));
});
test("search and kind filters are case-insensitive and compose", () => {
  assert.equal(matchingResources(resources, "API", "controllers").length, 2);
  assert.equal(matchingResources(resources, " gateway ", "Service").length, 1);
  assert.equal(matchingResources(resources, "missing", "all").length, 0);
  assert.equal(resourceId(resources[0]), "demo/Deployment/api");
});
test("cycles or absent resources do not hang layout", () => {
  const cyclic = [...connections, edge("Pod/api-pod", "Deployment/api", "owns")];
  assert.equal(layoutTopology(resources, cyclic).size, 5);
  assert.equal(layoutTopology([], []).size, 0);
});
