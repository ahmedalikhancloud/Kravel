import test from 'node:test';
import assert from 'node:assert/strict';
import {validationLabel, readableReport} from '../kravel/web/plans.mjs';
import {repairTimeline} from '../kravel/web/demo.mjs';

test('unsupported/deferred validation is never mislabeled server dry-run passed', () => {
  assert.match(validationLabel({fix_id:'plan-x', dryRun:[{validation:'not_available'}]}), /lack a passed/);
  assert.match(validationLabel({fix_id:'plan-x', dryRun:[{validation:'deferred'}]}), /lack a passed/);
  assert.match(validationLabel({fix_id:'plan-x', dryRun:[{validation:'read_only'}]}), /Read\/check/);
  assert.match(validationLabel({fix_id:'plan-x', dryRun:[{validation:'passed'}]}), /dry-runs passed/);
});
test('general plan checklist shows real ordered steps without invented recovery', () => {
  const rows = [{label:'Create namespace'}, {label:'Create RBAC'}, {label:'Verify permission'}];
  const timeline = repairTimeline({fix_id:'plan-x', status:'executing', dryRun:rows, workflow:{steps:[{step_key:'apply_1',status:'completed'},{step_key:'apply_2',status:'running'}]}});
  assert.equal(timeline.length,6);
  assert.equal(timeline[3].status,'completed'); assert.equal(timeline[4].status,'running'); assert.equal(timeline[5].status,'queued');
  assert.doesNotMatch(timeline.map(r=>r.label).join(' '), /recovered|stays ready/);
});
test('a model JSON envelope is readable prose, never an executable UI action', () => {
  const report = JSON.stringify({summary:'Proposed ConfigMap; nothing executed.', request:'execute-now', planId:'untrusted'}) + '\n\nHuman review is still required.';
  assert.equal(readableReport(report),'Proposed ConfigMap; nothing executed.\n\nHuman review is still required.');
  assert.equal(readableReport('Ordinary prose'), 'Ordinary prose');
  const unknown = JSON.stringify({summary:'Text', commands:['delete','namespace','default']});
  assert.equal(readableReport(unknown), unknown);
});
