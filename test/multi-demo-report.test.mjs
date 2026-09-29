import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { TemporalStore } from "../src/store.mjs";

const namespace = "kravel-demo";

function metadata(name, uid, resourceVersion, changeAt) {
  return {
    namespace,
    name,
    uid,
    resourceVersion,
    ...(changeAt ? { annotations: { "kravel.dev/change-at": changeAt } } : {})
  };
}

function configMap(mode, resourceVersion, changeAt) {
  return {
    apiVersion: "v1",
    kind: "ConfigMap",
    metadata: metadata("api-config", "config-uid", resourceVersion, changeAt),
    data: { STARTUP_MODE: mode, DATABASE_TIMEOUT_MS: mode === "healthy" ? "3000" : "3" }
  };
}

function service(selector, resourceVersion, changeAt) {
  return {
    apiVersion: "v1",
    kind: "Service",
    metadata: metadata("payments-api", "service-uid", resourceVersion, changeAt),
    spec: { selector: { app: selector }, ports: [{ port: 80, targetPort: 8080 }] }
  };
}

function endpoints(addresses, resourceVersion) {
  return {
    apiVersion: "v1",
    kind: "Endpoints",
    metadata: metadata("payments-api", "endpoints-uid", resourceVersion),
    ...(addresses.length ? { subsets: [{ addresses: addresses.map((ip) => ({ ip })), ports: [{ port: 8080 }] }] } : {})
  };
}

function deployment(name, image, resourceVersion, { changeAt, nodeSelector } = {}) {
  return {
    apiVersion: "apps/v1",
    kind: "Deployment",
    metadata: metadata(name, `${name}-uid`, resourceVersion),
    spec: {
      template: {
        metadata: {
          labels: { app: name },
          ...(changeAt ? { annotations: { "kravel.dev/change-at": changeAt } } : {})
        },
        spec: {
          containers: [{ name, image }],
          ...(nodeSelector ? { nodeSelector } : {})
        }
      }
    }
  };
}

function warning(uid, eventTime, podName, reason, note) {
  return {
    metadata: { namespace, uid },
    eventTime,
    regarding: { kind: "Pod", name: podName, uid: `${podName}-uid` },
    type: "Warning",
    reason,
    note
  };
}

test("multi-incident report reconstructs four independent production failures", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "kravel-multi-report-"));
  const dbPath = path.join(directory, "report.db");
  const store = new TemporalStore(dbPath);
  const clusterId = "docker-desktop";

  try {
    store.recordResourceChange({ clusterId, action: "ADDED", object: configMap("healthy", "1"), eventAt: "2026-09-28T12:00:00Z" });
    store.recordResourceChange({ clusterId, action: "ADDED", object: service("payments-api", "1"), eventAt: "2026-09-28T12:00:01Z" });
    store.recordResourceChange({ clusterId, action: "ADDED", object: endpoints(["10.42.0.10", "10.42.0.11"], "1"), eventAt: "2026-09-28T12:00:02Z" });
    store.recordResourceChange({ clusterId, action: "ADDED", object: deployment("inventory-api", "busybox:1.36", "1"), eventAt: "2026-09-28T12:00:03Z" });
    store.recordResourceChange({ clusterId, action: "ADDED", object: deployment("reports-worker", "busybox:1.36", "1"), eventAt: "2026-09-28T12:00:04Z" });

    store.recordResourceChange({ clusterId, action: "MODIFIED", object: configMap("broken", "2", "2026-09-28T12:01:00Z"), eventAt: "2026-09-28T12:01:01Z" });
    store.recordKubernetesEvent(clusterId, warning("crash-event", "2026-09-28T12:01:20Z", "checkout-api-abc", "BackOff", "Back-off restarting failed container"));

    store.recordResourceChange({ clusterId, action: "MODIFIED", object: service("payments-api-v2", "2", "2026-09-28T12:02:00Z"), eventAt: "2026-09-28T12:02:01Z" });
    store.recordResourceChange({ clusterId, action: "MODIFIED", object: endpoints([], "2"), eventAt: "2026-09-28T12:02:02Z" });

    store.recordResourceChange({
      clusterId,
      action: "MODIFIED",
      object: deployment("inventory-api", "busybox:kravel-demo-image-does-not-exist", "2", { changeAt: "2026-09-28T12:03:00Z" }),
      eventAt: "2026-09-28T12:03:01Z"
    });
    store.recordKubernetesEvent(clusterId, warning("image-event", "2026-09-28T12:03:20Z", "inventory-api-def", "Failed", "Failed to pull image"));

    store.recordResourceChange({
      clusterId,
      action: "MODIFIED",
      object: deployment("reports-worker", "busybox:1.36", "2", {
        changeAt: "2026-09-28T12:04:00Z",
        nodeSelector: { "kravel.dev/nonexistent-node": "true" }
      }),
      eventAt: "2026-09-28T12:04:01Z"
    });
    store.recordKubernetesEvent(clusterId, warning("schedule-event", "2026-09-28T12:04:20Z", "reports-worker-ghi", "FailedScheduling", "0/2 nodes didn't match Pod's node selector"));
  } finally {
    store.close();
  }

  try {
    const result = spawnSync(process.execPath, ["src/multi-demo-report.mjs", "2026-09-28T12:00:30Z", "2026-09-28T12:05:00Z", namespace], {
      cwd: process.cwd(),
      encoding: "utf8",
      env: { ...process.env, KRAVEL_DB_PATH: dbPath, KRAVEL_CLUSTER_ID: clusterId }
    });

    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /Configuration regression -> CrashLoopBackOff/);
    assert.match(result.stdout, /STARTUP_MODE healthy -> broken/);
    assert.match(result.stdout, /Service selector drift -> zero backends/);
    assert.match(result.stdout, /2 ready addresses -> 0/);
    assert.match(result.stdout, /Bad image rollout -> ImagePullBackOff/);
    assert.match(result.stdout, /busybox:1\.36 -> busybox:kravel-demo-image-does-not-exist/);
    assert.match(result.stdout, /Impossible node selector -> FailedScheduling/);
    assert.match(result.stdout, /kravel\.dev\/nonexistent-node/);
    assert.match(result.stdout, /5\/5 expected state transitions were reconstructed/);
    assert.match(result.stdout, /Kubernetes often emits no Warning Event for selector drift/);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
