import { performance } from "node:perf_hooks";

const severeDiagnoses = new Set(["service_selector_drift", "bad_image_rollout"]);

const runbooks = {
  config_regression: {
    title: "Configuration regression containment",
    checks: [
      "Verify the changed ConfigMap keys against the last known-good state.",
      "Confirm the affected workloads actually consume the ConfigMap.",
      "Check whether a rollback would overwrite any later approved changes."
    ],
    proposedActions: [
      "Restore only the confirmed regressed keys from the approved last-known-good revision.",
      "Restart the consuming workload only after the configuration change is approved.",
      "Watch rollout health and compare restart counts against the incident baseline."
    ]
  },
  service_selector_drift: {
    title: "Service backend restoration",
    checks: [
      "Compare the Service selector with ready Pod labels at the incident timestamp.",
      "Confirm EndpointSlice membership dropped after the selector change."
    ],
    proposedActions: [
      "Restore the last approved selector or correct the workload labels.",
      "Verify ready endpoints before declaring recovery."
    ]
  },
  bad_image_rollout: {
    title: "Bad image rollout containment",
    checks: [
      "Confirm the image reference changed immediately before pull failures.",
      "Verify the previous image digest and registry availability."
    ],
    proposedActions: [
      "Roll back to the last approved immutable image digest.",
      "Verify pull success and readiness before resuming the rollout."
    ]
  },
  scheduling_constraint: {
    title: "Scheduling constraint correction",
    checks: [
      "Compare node selector, affinity, taints, and resource requests with available nodes.",
      "Verify whether the constraint was introduced in the incident window."
    ],
    proposedActions: [
      "Remove or correct only the confirmed impossible scheduling constraint.",
      "Verify the replacement Pod schedules before approving wider rollout."
    ]
  }
};

const automationSignals = {
  config_regression: {
    change: /ConfigMap|\/data\/|envFrom|configMap/i,
    event: /BackOff|CrashLoop|Failed|Unhealthy/i
  },
  service_selector_drift: {
    change: /Service|Endpoint|selector|subsets/i,
    event: /Endpoint|Unhealthy|Failed/i
  },
  bad_image_rollout: {
    change: /Deployment|StatefulSet|DaemonSet|containers|image/i,
    event: /ImagePull|ErrImage|Pulling|Failed/i
  },
  scheduling_constraint: {
    change: /Deployment|StatefulSet|DaemonSet|nodeSelector|affinity|resources/i,
    event: /FailedScheduling|Unschedulable/i
  }
};

function boundedThreshold(value, fallback) {
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 && number <= 1 ? number : fallback;
}

export function decidePolicy({ diagnosis, confidence }, configuration = {}) {
  const started = performance.now();
  const highConfidence = boundedThreshold(configuration.highConfidence, 0.85);
  const minimumMargin = boundedThreshold(configuration.minimumMargin, 0.2);
  const positiveThreshold = boundedThreshold(configuration.positiveThreshold, 0.65);
  const ranked = Object.entries(diagnosis ?? {})
    .map(([name, probability]) => ({ name, probability: Number(probability) }))
    .filter((item) => Number.isFinite(item.probability))
    .sort((left, right) => right.probability - left.probability);
  if (!ranked.length) throw new Error("Policy gate received no classification probabilities");
  const top = ranked[0];
  const runnerUp = ranked[1]?.probability ?? 0;
  const positives = ranked.filter((item) => item.probability >= positiveThreshold);
  const margin = top.probability - runnerUp;
  const severe = severeDiagnoses.has(top.name) && top.probability >= positiveThreshold;
  const ambiguous = top.probability < highConfidence
    || margin < minimumMargin
    || positives.length > 1
    || (confidence !== null && Number(confidence) < highConfidence);
  const route = severe || ambiguous ? "qwen_investigation" : "predefined_runbook";
  const reasons = [];
  if (severe) reasons.push("severe_incident_class");
  if (top.probability < highConfidence) reasons.push("top_probability_below_threshold");
  if (margin < minimumMargin) reasons.push("classification_margin_too_small");
  if (positives.length > 1) reasons.push("multiple_positive_classes");
  if (confidence !== null && Number(confidence) < highConfidence) reasons.push("classifier_confidence_below_threshold");
  if (!reasons.length) reasons.push("routine_high_confidence");
  return {
    route,
    topDiagnosis: top.name,
    topProbability: top.probability,
    margin,
    positiveDiagnoses: positives.map((item) => item.name),
    severe,
    ambiguous,
    reasons,
    thresholds: { highConfidence, minimumMargin, positiveThreshold },
    latencyMs: performance.now() - started
  };
}

export function runPredefinedAutomation({ store, clusterId, baselineAt, incidentAt, namespace, diagnosis }) {
  const started = performance.now();
  const profile = automationSignals[diagnosis];
  if (!profile) throw new Error(`No predefined diagnostic automation exists for ${diagnosis}`);
  const diff = store.diffStates({ clusterId, from: baselineAt, to: incidentAt, namespace });
  const lookback = Math.max(120, Math.ceil((new Date(incidentAt) - new Date(baselineAt)) / 1000) + 10);
  const context = store.contextShard({ clusterId, incidentAt, lookback, namespace, limit: 100 });
  const matchedChanges = diff.changes
    .filter((change) => profile.change.test(`${change.resourceKey} ${change.changedPaths.join(" ")}`))
    .slice(0, 12)
    .map((change) => ({
      resourceKey: change.resourceKey,
      changeType: change.changeType,
      changedPaths: change.changedPaths.slice(0, 12)
    }));
  const matchedEvents = context.kubernetesEvents
    .filter((event) => profile.event.test(`${event.reason} ${event.note}`))
    .slice(-12)
    .map((event) => ({
      eventAt: event.eventAt,
      reason: event.reason,
      resourceKey: [event.regardingApiVersion, event.regardingKind, event.namespace, event.regardingName].join("|")
    }));
  return {
    value: {
      status: "completed",
      executionMode: "read_only_diagnostics",
      runbook: diagnosis,
      checksExecuted: ["temporal_state_diff", "incident_event_correlation"],
      matchedChanges,
      matchedEvents,
      remediationExecuted: false
    },
    latencyMs: performance.now() - started
  };
}

export function createHumanReviewProposal({ policy, diagnosis, predefinedAutomation = null, qwenReport = "", qwenGuardrail = null }) {
  const started = performance.now();
  const relevant = policy.positiveDiagnoses.length ? policy.positiveDiagnoses : [policy.topDiagnosis];
  const selectedRunbooks = relevant.map((name) => ({ diagnosis: name, ...runbooks[name] })).filter((item) => item.title);
  return {
    value: {
      status: "awaiting_human_review",
      executionMode: "dry_run",
      diagnosticAutomationExecuted: Boolean(predefinedAutomation),
      remediationExecuted: false,
      route: policy.route,
      leadingDiagnosis: policy.topDiagnosis,
      probabilities: Object.fromEntries(relevant.map((name) => [name, diagnosis[name]])),
      policyReasons: policy.reasons,
      runbooks: selectedRunbooks,
      predefinedAutomation,
      qwenInvestigation: qwenReport || null,
      qwenOutputGuardrail: qwenGuardrail
        ? { decision: qwenGuardrail.decision, findings: qwenGuardrail.findings }
        : null,
      approvalRequirement: "A human must validate the evidence, blast radius, rollback target, and commands before any mutation."
    },
    latencyMs: performance.now() - started
  };
}
