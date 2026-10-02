// Pure presentation helpers. Display state is always derived from real observations.
export const resourceHelp = {
  Pod: 'A Pod is the small home where an application container runs.',
  Deployment: 'A Deployment keeps the right number of Pods running and manages updates.',
  DaemonSet: 'A DaemonSet runs a Pod on each eligible node, often for logging or monitoring.',
  StatefulSet: 'A StatefulSet manages ordered Pods with stable identities, often for stateful applications.',
  ReplicaSet: 'A ReplicaSet is a Deployment’s helper: it maintains a set of matching Pods.',
  ConfigMap: 'A ConfigMap holds settings that an application can read. It is not a secret store.',
  Service: 'A Service gives matching Pods a stable network address. A connection here is not a traffic test.',
};

export function sessionItems(items, session, timestamp = 'started_at') {
  const since = Date.parse(session?.startedAt || '');
  if (!Number.isFinite(since)) return [];
  return items.filter((item) => Date.parse(item[timestamp]) >= since || ['pending', 'approved', 'executing', 'running'].includes(item.status));
}

export function responseTitle(payload) {
  return payload.responseKind === 'request_blocked' ? 'Request paused · safety check' : payload.responseKind === 'scope_help' ? 'Karl’s guide · no cluster reads' : payload.responseKind === 'learning_explanation' ? 'Learn with Karl · general explanation' : 'Karl’s analysis · check the evidence';
}

export function resourceIssues(cluster, kind, name) {
  const connected = new Set([`${cluster?.namespace}/${kind}/${name}`]);
  for (let depth = 0; depth < 3; depth++) {
    for (const edge of cluster?.connections || []) {
      if (connected.has(edge.source) && ['owns', 'configures'].includes(edge.relation)) connected.add(edge.target);
    }
  }
  return (cluster?.issues || []).filter((issue) => connected.has(`${cluster?.namespace}/${issue.resource}`));
}

export function repairTimeline(proposal) {
  const steps = new Map((proposal.workflow?.steps || []).map((s) => [s.step_key, s]));
  const verification = new Map((proposal.verification?.steps || []).map((s) => [s.step_key, s]));
  const stopped = ['failed', 'expired', 'rejected'].includes(proposal.status);
  const stage = (key, label, source = steps) => ({step_key: key, label, status: source.get(key)?.status || (stopped ? 'skipped' : 'queued'), duration_ms: source.get(key)?.duration_ms || 0, details: source.get(key)?.details || {}});
  const plan = [stage('dry_run', 'Preview the change safely'), stage('approval', 'Wait for your approval'), stage('revalidate', 'Check that the reviewed objects have not changed')];
  for (let index = 0; index < (proposal.dryRun || []).length; index++) {
    const resource = proposal.dryRun[index].resource || `operation ${index + 1}`;
    const label = proposal.fix_id === 'fix_bad_configmap' ? index === 0 ? 'Restore the ConfigMap setting' : 'Restart Pods with the corrected setting' : `Apply the approved change · ${resource}`;
    plan.push(stage(`apply_${index + 1}`, label));
  }
  plan.push(stage('rollout', 'Wait for the updated workload to become ready', verification), stage('stability', 'Confirm it stays ready · three checks', verification));
  if (proposal.verification && ['failed', 'timeout', 'interrupted'].includes(proposal.verification.status)) {
    for (const s of plan.slice(-2)) if (s.status === 'queued') s.status = 'skipped';
  }
  return plan;
}
