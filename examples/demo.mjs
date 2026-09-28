import { TemporalStore } from "../src/store.mjs";

const store = new TemporalStore(":memory:");
const clusterId = "demo";
const config = (timeout, resourceVersion) => ({
  apiVersion: "v1",
  kind: "ConfigMap",
  metadata: { namespace: "checkout", name: "api-config", uid: "config-1", resourceVersion },
  data: { DATABASE_TIMEOUT: `${timeout}s` }
});

store.recordResourceChange({ clusterId, action: "ADDED", object: config(30, "1"), eventAt: "2026-09-28T16:00:00Z" });
store.recordResourceChange({
  clusterId, action: "MODIFIED", object: config(3, "2"), actor: "deploy-bot",
  eventAt: "2026-09-28T16:05:00Z"
});
store.recordKubernetesEvent(clusterId, {
  metadata: { uid: "event-1", namespace: "checkout" },
  eventTime: "2026-09-28T16:08:00Z",
  regarding: { kind: "Pod", name: "api-7dc9", uid: "pod-1" },
  reason: "BackOff", type: "Warning", note: "Back-off restarting failed container"
});
store.recordMetricSample(clusterId, "pod_restarts", "2026-09-28T16:08:00Z", 5, { namespace: "checkout", pod: "api-7dc9" });

console.log("\nState before the edit:\n", JSON.stringify(store.stateAt({ clusterId, timestamp: "2026-09-28T16:04:00Z" }), null, 2));
console.log("\nState diff:\n", JSON.stringify(store.diffStates({ clusterId, from: "2026-09-28T16:04:00Z", to: "2026-09-28T16:06:00Z" }), null, 2));
console.log("\nIncident context shard:\n", JSON.stringify(store.contextShard({ clusterId, incidentAt: "2026-09-28T16:09:00Z", lookback: "10m", namespace: "checkout" }), null, 2));
store.close();
