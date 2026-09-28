import { loadConfig } from "./config.mjs";
import { TemporalStore } from "./store.mjs";

const [baselineAt, incidentAt, namespace = "kravel-demo"] = process.argv.slice(2);
if (!baselineAt || !incidentAt) {
  console.error("usage: node src/multi-demo-report.mjs <baseline-at> <incident-at> [namespace]");
  process.exit(2);
}

const config = loadConfig();
const store = new TemporalStore(config.dbPath);

function findObject(state, kind, name) {
  return state.objects.find((object) => object.kind === kind && object.metadata?.name === name);
}

function containerImage(object, name) {
  return object?.spec?.template?.spec?.containers?.find((container) => container.name === name)?.image;
}

function endpointCount(object) {
  return (object?.subsets ?? []).reduce((count, subset) => count + (subset.addresses?.length ?? 0), 0);
}

function requestedAt(object) {
  return object?.metadata?.annotations?.["kravel.dev/change-at"]
    ?? object?.spec?.template?.metadata?.annotations?.["kravel.dev/change-at"]
    ?? "<not marked>";
}

function firstChange(changes, kind, name, pathFragment) {
  return [...changes]
    .filter((change) => change.kind === kind && change.name === name
      && change.patch.some((operation) => operation.path.includes(pathFragment)))
    .sort((left, right) => left.eventAt.localeCompare(right.eventAt))[0];
}

function firstEvent(events, predicate) {
  return [...events].filter(predicate).sort((left, right) => left.eventAt.localeCompare(right.eventAt))[0];
}

function observedAt(change) {
  return change?.eventAt ?? "<not observed>";
}

function sinceBaseline(timestamp) {
  if (!timestamp || timestamp.startsWith("<")) return "unknown offset";
  const seconds = Math.max(0, Math.round((new Date(timestamp) - new Date(baselineAt)) / 1000));
  return `T+${seconds}s`;
}

try {
  const before = store.stateAt({ clusterId: config.clusterId, timestamp: baselineAt, namespace });
  const after = store.stateAt({ clusterId: config.clusterId, timestamp: incidentAt, namespace });
  const windowSeconds = Math.max(120, Math.ceil((new Date(incidentAt) - new Date(baselineAt)) / 1000) + 5);
  const context = store.contextShard({
    clusterId: config.clusterId,
    incidentAt,
    lookback: windowSeconds,
    namespace,
    limit: 500
  });

  const oldConfig = findObject(before, "ConfigMap", "api-config");
  const newConfig = findObject(after, "ConfigMap", "api-config");
  const oldService = findObject(before, "Service", "payments-api");
  const newService = findObject(after, "Service", "payments-api");
  const oldEndpoints = findObject(before, "Endpoints", "payments-api");
  const newEndpoints = findObject(after, "Endpoints", "payments-api");
  const oldInventory = findObject(before, "Deployment", "inventory-api");
  const newInventory = findObject(after, "Deployment", "inventory-api");
  const oldReports = findObject(before, "Deployment", "reports-worker");
  const newReports = findObject(after, "Deployment", "reports-worker");

  const configChange = firstChange(context.changes, "ConfigMap", "api-config", "/data/STARTUP_MODE");
  const serviceChange = firstChange(context.changes, "Service", "payments-api", "/spec/selector");
  const endpointChange = firstChange(context.changes, "Endpoints", "payments-api", "/subsets");
  const imageChange = firstChange(context.changes, "Deployment", "inventory-api", "/spec/template/spec/containers");
  const schedulingChange = firstChange(context.changes, "Deployment", "reports-worker", "/spec/template/spec/nodeSelector");

  const crashEvent = firstEvent(context.kubernetesEvents, (event) =>
    event.regardingName.startsWith("checkout-api-")
    && (event.reason === "BackOff" || /back-off|failed/i.test(event.note))
  );
  const imageEvent = firstEvent(context.kubernetesEvents, (event) =>
    event.regardingName.startsWith("inventory-api-")
    && (/pull|image/i.test(event.reason) || /pull|image/i.test(event.note))
  );
  const schedulingEvent = firstEvent(context.kubernetesEvents, (event) =>
    event.regardingName.startsWith("reports-worker-")
    && (event.reason === "FailedScheduling" || /node selector|didn't match|unschedulable/i.test(event.note))
  );

  console.log("\nKRAVEL MULTI-INCIDENT TIME-TRAVEL REPORT");
  console.log("========================================");
  console.log(`Cluster:   ${config.clusterId}`);
  console.log(`Namespace: ${namespace}`);
  console.log(`Baseline:  ${baselineAt}`);
  console.log(`Final:     ${incidentAt}`);

  console.log("\n1. Configuration regression -> CrashLoopBackOff");
  console.log(`   Marked change:  ${requestedAt(newConfig)}`);
  console.log(`   Observed change: ${observedAt(configChange)} (${sinceBaseline(observedAt(configChange))})`);
  console.log(`   State rewind:    STARTUP_MODE ${oldConfig?.data?.STARTUP_MODE ?? "<missing>"} -> ${newConfig?.data?.STARTUP_MODE ?? "<missing>"}`);
  console.log(`   First symptom:   ${crashEvent?.eventAt ?? "<not observed>"} ${crashEvent ? `${crashEvent.reason} — ${crashEvent.note}` : ""}`);

  console.log("\n2. Service selector drift -> zero backends (silent failure)");
  console.log(`   Marked change:  ${requestedAt(newService)}`);
  console.log(`   Observed change: ${observedAt(serviceChange)} (${sinceBaseline(observedAt(serviceChange))})`);
  console.log(`   State rewind:    selector app=${oldService?.spec?.selector?.app ?? "<missing>"} -> app=${newService?.spec?.selector?.app ?? "<missing>"}`);
  console.log(`   Endpoint proof:  ${endpointCount(oldEndpoints)} ready addresses -> ${endpointCount(newEndpoints)}; endpoint update observed ${observedAt(endpointChange)}`);
  console.log("   Kubernetes often emits no Warning Event for selector drift; the state transition is the evidence.");

  console.log("\n3. Bad image rollout -> ImagePullBackOff");
  console.log(`   Marked change:  ${requestedAt(newInventory)}`);
  console.log(`   Observed change: ${observedAt(imageChange)} (${sinceBaseline(observedAt(imageChange))})`);
  console.log(`   State rewind:    image ${containerImage(oldInventory, "inventory-api") ?? "<missing>"} -> ${containerImage(newInventory, "inventory-api") ?? "<missing>"}`);
  console.log(`   First symptom:   ${imageEvent?.eventAt ?? "<not observed>"} ${imageEvent ? `${imageEvent.reason} — ${imageEvent.note}` : ""}`);

  console.log("\n4. Impossible node selector -> FailedScheduling");
  console.log(`   Marked change:  ${requestedAt(newReports)}`);
  console.log(`   Observed change: ${observedAt(schedulingChange)} (${sinceBaseline(observedAt(schedulingChange))})`);
  console.log(`   State rewind:    nodeSelector ${JSON.stringify(oldReports?.spec?.template?.spec?.nodeSelector ?? {})} -> ${JSON.stringify(newReports?.spec?.template?.spec?.nodeSelector ?? {})}`);
  console.log(`   First symptom:   ${schedulingEvent?.eventAt ?? "<not observed>"} ${schedulingEvent ? `${schedulingEvent.reason} — ${schedulingEvent.note}` : ""}`);

  console.log("\nOrdered causal candidates");
  const timeline = [
    { at: configChange?.eventAt, text: "ConfigMap api-config entered broken startup mode" },
    { at: crashEvent?.eventAt, text: "checkout-api began crash-looping" },
    { at: serviceChange?.eventAt, text: "payments-api Service selector drifted" },
    { at: endpointChange?.eventAt, text: "payments-api endpoint set became empty" },
    { at: imageChange?.eventAt, text: "inventory-api image changed to a missing tag" },
    { at: imageEvent?.eventAt, text: "inventory-api image pull failed" },
    { at: schedulingChange?.eventAt, text: "reports-worker gained an impossible node selector" },
    { at: schedulingEvent?.eventAt, text: "scheduler rejected the replacement reports Pod" }
  ].filter((entry) => entry.at).sort((left, right) => left.at.localeCompare(right.at));
  for (const entry of timeline) console.log(`   ${entry.at}  ${entry.text}`);

  const evidenceCount = [configChange, serviceChange, endpointChange, imageChange, schedulingChange].filter(Boolean).length;
  console.log("\nEvidence quality");
  console.log(`   ${evidenceCount}/5 expected state transitions were reconstructed.`);
  console.log("   Marked time is when the scenario submitted each mutation; observed time is when Kravel received the watch event.");
  console.log("   Audit logging is not enabled in this fast playground, so actor identity and API-server request time are unavailable.");
} finally {
  store.close();
}
