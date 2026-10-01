from copy import deepcopy
import time

import pytest

from kravel.labs import LabController, operations
from kravel.operator import OperatorConsole
from kravel.store import AuditStore


class Kube:
    def __init__(self): self.calls, self.objects = [], {}
    def get_resource(self, kind, name, namespace):
        assert namespace == 'kravel-demo'
        obj = self.objects.setdefault((kind, name), {'metadata': {'uid': name, 'resourceVersion': '1'}, 'spec': {'replicas': 1}})
        return {'object': deepcopy(obj)}
    def patch(self, kind, name, namespace, patch, **kwargs):
        assert namespace == 'kravel-demo'
        self.calls.append((kind, name, deepcopy(patch), kwargs['dry_run']))
        return {'object': self.get_resource(kind, name, namespace)['object'], 'dryRun': kwargs['dry_run']}


@pytest.mark.parametrize('scenario', ['oom', 'imagepull', 'crashloop', 'configmap', 'network'])
def test_each_scenario_requires_dry_run_session_confirmation_and_is_one_use(scenario):
    kube = Kube()
    labs = LabController(OperatorConsole(kube, AuditStore()), idle_check=lambda: None)
    item = labs.preview(scenario, 'break', 'human-one')
    assert all(call[-1] for call in kube.calls)
    with pytest.raises(ValueError): labs.confirm(item['previewId'], 'different-human')
    result = labs.confirm(item['previewId'], 'human-one')
    assert result['status'] == 'accepted' and any(not call[-1] for call in kube.calls)
    with pytest.raises(ValueError): labs.confirm(item['previewId'], 'human-one')


def test_reset_is_fixed_and_bounded_to_named_labs():
    ops = operations('all', 'reset', 'fixed-stamp')
    assert len(ops) == 6
    assert {op['name'] for op in ops} == {'oom-demo', 'image-demo', 'crash-demo', 'config-demo', 'demo-gateway'}
    assert all(set(op) == {'kind', 'name', 'patch'} for op in ops)
    for scenario, action in [('production', 'break'), ('oom', 'delete'), ('all', 'break')]:
        with pytest.raises(ValueError): operations(scenario, action, 'stamp')


def test_stale_expired_and_active_workflow_previews_cannot_execute():
    kube = Kube()
    labs = LabController(OperatorConsole(kube, AuditStore()), idle_check=lambda: None)
    preview = labs.preview('configmap', 'break', 'human')
    kube.objects[('deployments', 'config-demo')]['spec']['replicas'] = 2
    with pytest.raises(ValueError, match='changed'): labs.confirm(preview['previewId'], 'human')
    assert all(call[-1] for call in kube.calls)  # All preconditions checked before first apply.
    preview = labs.preview('oom', 'break', 'human')
    labs.previews[preview['previewId']]['expires'] = time.monotonic()-1
    with pytest.raises(ValueError, match='expired'): labs.confirm(preview['previewId'], 'human')
    def busy(): raise ValueError('Active human approval')
    labs.idle_check = busy
    with pytest.raises(ValueError, match='approval'): labs.preview('oom', 'break', 'human')
    assert all(call[-1] for call in kube.calls)


def test_partial_failure_is_reported_without_retry_or_rollback():
    kube = Kube()
    labs = LabController(OperatorConsole(kube, AuditStore()), idle_check=lambda: None)
    preview = labs.preview('configmap', 'break', 'human')
    original = kube.patch
    def patch(kind, name, namespace, value, **kwargs):
        if kind == 'deployments' and not kwargs['dry_run']: raise RuntimeError('network failure')
        return original(kind, name, namespace, value, **kwargs)
    kube.patch = patch
    result = labs.confirm(preview['previewId'], 'human')
    assert result['status'] == 'partial_failure' and len(result['accepted']) == 1
    assert sum(not call[-1] for call in kube.calls) == 1
    assert 'lab.partial_failure' in [item['action'] for item in labs.store.audit_entries()]
