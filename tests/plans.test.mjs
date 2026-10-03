import test from 'node:test';
import assert from 'node:assert/strict';
import {validationLabel, readableReport, attachReviewSubmission} from '../kravel/web/plans.mjs';
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

class Element {
  constructor(tag) { this.tagName = tag; this.children = []; this.textContent = ''; this.handlers = {}; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  setAttribute() {}
  addEventListener(name, handler) { this.handlers[name] = handler; }
}

test('validation rejection stays inline, retains details, and never rejects the click handler', async () => {
  globalThis.document = {createElement: (tag) => new Element(tag)};
  try {
    const card = new Element('article'), button = new Element('button'), states = new Map(); button.textContent = 'Validate';
    const error = new Error('Step 1 failed server dry-run: cannot restore slice from map'); error.details = {reviewCreated:false, validation:{step:1,validation:'failed'}};
    attachReviewSubmission(card, button, {id:'run',status:'completed',payload:{}}, 'plan-id', async () => { throw error; }, states);
    await button.handlers.click();
    assert.equal(states.get('run/plan-id').status, 'failed'); assert.equal(button.disabled, false);
    const text = JSON.stringify(card.children);
    assert.match(text, /cannot restore slice from map/); assert.match(text, /No approval request was created/); assert.match(text, /Kubernetes validation details/);
  } finally { delete globalThis.document; }
});

test('lost delivery disables resubmission and successful delivery cannot double-submit', async () => {
  globalThis.document = {createElement: (tag) => new Element(tag)};
  try {
    for (const unknown of [false, true]) {
      let calls = 0; const card = new Element('article'), button = new Element('button'); button.textContent = 'Validate';
      attachReviewSubmission(card, button, {id:'run',status:'completed',payload:{}}, 'plan', async () => { calls++; if (unknown) throw new Error('Transport unavailable'); return {id:'proposal'}; });
      await button.handlers.click(); await button.handlers.click();
      assert.equal(calls, 1); assert.equal(button.disabled, true);
      assert.match(JSON.stringify(card.children), unknown ? /Delivery is uncertain/ : /Review requested/);
    }
  } finally { delete globalThis.document; }
});
