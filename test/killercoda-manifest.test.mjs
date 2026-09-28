import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

test("Killercoda pins the locally imported Kravel image to controlplane", () => {
  const manifest = fs.readFileSync("deploy/killercoda.yaml", "utf8");
  assert.match(manifest, /imagePullPolicy: Never/);
  assert.match(manifest, /nodeSelector:\n\s+kubernetes\.io\/hostname: controlplane/);
  assert.match(manifest, /key: node-role\.kubernetes\.io\/control-plane/);
});

test("Killercoda grants read-only access to historical Service backend evidence", () => {
  const manifest = fs.readFileSync("deploy/killercoda.yaml", "utf8");
  assert.match(manifest, /resources: \["pods", "configmaps", "services", "endpoints"/);
  assert.match(manifest, /apiGroups: \["discovery\.k8s\.io"\]/);
  assert.match(manifest, /resources: \["endpointslices"\]/);
  assert.match(manifest, /verbs: \["get", "list", "watch"\]/);
});
