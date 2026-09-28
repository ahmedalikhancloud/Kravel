import { loadConfig } from "./config.mjs";
import { objectIdentity } from "./kubernetes-object.mjs";
import { TemporalStore } from "./store.mjs";

const [baselineAt, incidentAt, namespace = "kravel-demo"] = process.argv.slice(2);
if (!baselineAt || !incidentAt) {
  console.error("usage: node src/demo-report.mjs <baseline-at> <incident-at> [namespace]");
  process.exit(2);
}

const config = loadConfig();
const store = new TemporalStore(config.dbPath);

function findObject(state, kind, name) {
  return state.objects.find((object) => object.kind === kind && object.metadata?.name === name);
}

function tMinus(timestamp) {
  const seconds = Math.max(0, Math.round((new Date(incidentAt) - new Date(timestamp)) / 1000));
  if (seconds < 60) return `T-${seconds}s`;
  return `T-${Math.floor(seconds / 60)}m${String(seconds % 60).padStart(2, "0")}s`;
}

function meaningfulPaths(paths) {
  return paths.filter((path) => ![
    "/metadata/resourceVersion",
    "/metadata/generation"
  ].includes(path) && !path.startsWith("/status"));
}

try {
  const before = store.stateAt({ clusterId: config.clusterId, timestamp: baselineAt, namespace });
  const after = store.stateAt({ clusterId: config.clusterId, timestamp: incidentAt, namespace });
  const diff = store.diffStates({ clusterId: config.clusterId, from: baselineAt, to: incidentAt, namespace });
  const windowSeconds = Math.max(60, Math.ceil((new Date(incidentAt) - new Date(baselineAt)) / 1000) + 2);
  const context = store.contextShard({
    clusterId: config.clusterId,
    incidentAt,
    lookback: windowSeconds,
    namespace,
    limit: 200
  });

  const oldConfig = findObject(before, "ConfigMap", "api-config");
  const newConfig = findObject(after, "ConfigMap", "api-config");
  const configChanged = oldConfig?.data?.STARTUP_MODE !== newConfig?.data?.STARTUP_MODE;
  const rolloutChange = context.changes.find((change) =>
    change.kind === "Deployment"
    && change.name === "checkout-api"
    && change.patch.some((operation) =>
      operation.path.includes("restartedAt")
      || (operation.path.endsWith("/annotations") && operation.value?.["kubectl.kubernetes.io/restartedAt"])
    )
  );
  const crashEvent = context.kubernetesEvents.find((event) =>
    ["BackOff", "Failed", "Unhealthy"].includes(event.reason)
    || /back-off|failed|unhealthy/i.test(event.note)
  );

  console.log("\nKRAVEL TIME-TRAVEL INCIDENT REPORT");
  console.log("==================================");
  console.log(`Cluster:   ${config.clusterId}`);
  console.log(`Namespace: ${namespace}`);
  console.log(`Baseline:  ${baselineAt}`);
  console.log(`Incident:  ${incidentAt}`);

  console.log("\n1. Reconstructed ConfigMap state");
  console.log(`   Before: STARTUP_MODE=${oldConfig?.data?.STARTUP_MODE ?? "<not observed>"}, DATABASE_TIMEOUT_MS=${oldConfig?.data?.DATABASE_TIMEOUT_MS ?? "<not observed>"}`);
  console.log(`   After:  STARTUP_MODE=${newConfig?.data?.STARTUP_MODE ?? "<deleted>"}, DATABASE_TIMEOUT_MS=${newConfig?.data?.DATABASE_TIMEOUT_MS ?? "<deleted>"}`);

  console.log("\n2. Deterministic state diff");
  for (const change of diff.changes) {
    const paths = meaningfulPaths(change.changedPaths);
    if (!paths.length && change.changeType === "modified") continue;
    console.log(`   ${change.changeType.toUpperCase().padEnd(8)} ${change.resourceKey}`);
    if (paths.length) console.log(`            ${paths.join(", ")}`);
  }

  console.log("\n3. Evidence timeline");
  const timeline = context.changes
    .filter((change) => meaningfulPaths(change.patch.map((operation) => operation.path)).length)
    .sort((left, right) => left.eventAt.localeCompare(right.eventAt));
  for (const change of timeline) {
    const paths = meaningfulPaths(change.patch.map((operation) => operation.path));
    console.log(`   ${tMinus(change.eventAt).padEnd(8)} ${change.action.padEnd(8)} ${change.kind} ${change.namespace}/${change.name}`);
    console.log(`            ${paths.join(", ")}`);
  }
  for (const event of [...context.kubernetesEvents].sort((left, right) => left.eventAt.localeCompare(right.eventAt))) {
    if (!["Warning", "Error"].includes(event.type) && !["BackOff", "Failed", "Unhealthy"].includes(event.reason)) continue;
    console.log(`   ${tMinus(event.eventAt).padEnd(8)} EVENT    ${event.regardingKind} ${event.regardingName}: ${event.reason} — ${event.note}`);
  }

  console.log("\n4. Most plausible chain (correlation, not proof)");
  if (configChanged) {
    console.log(`   ConfigMap api-config changed STARTUP_MODE from ${oldConfig?.data?.STARTUP_MODE} to ${newConfig?.data?.STARTUP_MODE}.`);
  } else {
    console.log("   No STARTUP_MODE change was reconstructed in the selected window.");
  }
  console.log(rolloutChange
    ? "   The checkout-api Deployment was restarted after the configuration change."
    : "   A rollout-restart change was not observed in the selected window.");
  console.log(crashEvent
    ? `   Kubernetes then reported ${crashEvent.reason} for ${crashEvent.regardingKind} ${crashEvent.regardingName}.`
    : "   No explicit crash warning was observed yet; Pod state changes remain in the diff above.");
  console.log("\nConclusion:");
  console.log(configChanged && rolloutChange
    ? "   The configuration regression is the leading causal candidate. Validate against application logs before declaring root cause."
    : "   Evidence is incomplete; do not claim a root cause yet.");

  console.log("\nReconstruction proof:");
  console.log(`   ${before.objectCount} objects existed at the baseline and ${after.objectCount} at the incident timestamp.`);
  const keys = before.objects.slice(0, 3).map((object) => objectIdentity(object).key);
  if (keys.length) console.log(`   Example baseline keys: ${keys.join("; ")}`);
} finally {
  store.close();
}
