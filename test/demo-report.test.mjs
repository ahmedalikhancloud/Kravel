import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { TemporalStore } from "../src/store.mjs";

function configMap(mode, timeout, resourceVersion) {
  return {
    apiVersion: "v1",
    kind: "ConfigMap",
    metadata: { namespace: "kravel-demo", name: "api-config", uid: "cm-demo", resourceVersion },
    data: { STARTUP_MODE: mode, DATABASE_TIMEOUT_MS: timeout }
  };
}

function deployment(resourceVersion, restartedAt) {
  return {
    apiVersion: "apps/v1",
    kind: "Deployment",
    metadata: { namespace: "kravel-demo", name: "checkout-api", uid: "deploy-demo", resourceVersion },
    spec: {
      template: {
        metadata: { labels: { app: "checkout-api" }, ...(restartedAt ? { annotations: { "kubectl.kubernetes.io/restartedAt": restartedAt } } : {}) },
        spec: { containers: [{ name: "checkout-api", image: "busybox:1.36", envFrom: [{ configMapRef: { name: "api-config" } }] }] }
      }
    }
  };
}

test("deterministic report explains the controlled configuration regression", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "kravel-report-"));
  const dbPath = path.join(directory, "report.db");
  const store = new TemporalStore(dbPath);
  const clusterId = "docker-desktop";
  try {
    store.recordResourceChange({ clusterId, action: "ADDED", object: configMap("healthy", "3000", "1"), eventAt: "2026-09-28T11:59:00Z" });
    store.recordResourceChange({ clusterId, action: "ADDED", object: deployment("1"), eventAt: "2026-09-28T11:59:01Z" });
    store.recordResourceChange({ clusterId, action: "MODIFIED", object: configMap("broken", "3", "2"), eventAt: "2026-09-28T12:01:00Z" });
    store.recordResourceChange({ clusterId, action: "MODIFIED", object: deployment("2", "2026-09-28T12:01:01Z"), eventAt: "2026-09-28T12:01:01Z" });
    store.recordKubernetesEvent(clusterId, {
      metadata: { namespace: "kravel-demo", uid: "event-demo" },
      eventTime: "2026-09-28T12:01:30Z",
      regarding: { kind: "Pod", name: "checkout-api-broken", uid: "pod-broken" },
      type: "Warning",
      reason: "BackOff",
      note: "Back-off restarting failed container"
    });
  } finally {
    store.close();
  }

  try {
    const result = spawnSync(process.execPath, ["src/demo-report.mjs", "2026-09-28T12:00:00Z", "2026-09-28T12:02:00Z", "kravel-demo"], {
      cwd: process.cwd(),
      encoding: "utf8",
      env: { ...process.env, KRAVEL_DB_PATH: dbPath, KRAVEL_CLUSTER_ID: clusterId }
    });
    assert.equal(result.status, 0, result.stderr);
    assert.match(result.stdout, /Before: STARTUP_MODE=healthy/);
    assert.match(result.stdout, /After:  STARTUP_MODE=broken/);
    assert.match(result.stdout, /Deployment was restarted/);
    assert.match(result.stdout, /reported BackOff/);
    assert.match(result.stdout, /leading causal candidate/);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});
