import test from 'node:test';
import assert from 'node:assert/strict';
import {sessionItems, responseTitle, repairTimeline} from '../kravel/web/demo.mjs';

test('a fresh view hides old completed work but never hides active approvals', () => {
  const items = [{id: 'past', status: 'executed', created_at: '2026-09-29T00:00:00Z'}, {id: 'active', status: 'pending', created_at: '2026-09-29T00:00:00Z'}, {id: 'new', status: 'executed', created_at: '2026-09-30T12:01:00Z'}];
  assert.deepEqual(sessionItems(items, {startedAt: '2026-09-30T12:00:00Z'}, 'created_at').map((i) => i.id), ['active', 'new']);
  assert.deepEqual(sessionItems(items, null), []);
});

test('a redirected question is never labeled a grounded diagnosis', () => {
  assert.match(responseTitle({responseKind: 'scope_help'}), /no cluster reads/);
  assert.match(responseTitle({responseKind: 'request_blocked'}), /safety check/);
  assert.doesNotMatch(responseTitle({responseKind: 'model_synthesis'}), /Grounded/);
  assert.match(responseTitle({responseKind: 'learning_explanation'}), /general explanation/);
});

test('repair checkmarks require recorded completion, not merely patch acceptance', () => {
  const proposal = {fix_id: 'fix_bad_configmap', status: 'executed', dryRun: [{resource: 'ConfigMap/config-demo'}, {resource: 'Deployment/config-demo'}], workflow: {steps: [{step_key: 'dry_run', status: 'completed'}, {step_key: 'apply_1', status: 'completed'}, {step_key: 'apply_2', status: 'completed'}]}};
  const plan = repairTimeline(proposal);
  assert.equal(plan.length, 7);
  assert.equal(plan.at(-1).status, 'queued');
  assert.equal(plan.at(-2).status, 'queued');
  assert.match(plan[3].label, /ConfigMap/);
  proposal.verification = {status: 'running', steps: [{step_key: 'rollout', status: 'completed'}, {step_key: 'stability', status: 'running'}]};
  assert.equal(repairTimeline(proposal).at(-1).status, 'running');
  assert.equal(repairTimeline(proposal).at(-2).status, 'completed');
});

test('rejection leaves future repair stages skipped instead of showing success', () => {
  const plan = repairTimeline({status: 'rejected', dryRun: [{}], workflow: {steps: [{step_key: 'approval', status: 'rejected'}]}});
  assert.equal(plan[1].status, 'rejected');
  assert.equal(plan.at(-1).status, 'skipped');
});
