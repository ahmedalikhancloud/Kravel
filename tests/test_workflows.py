from contextlib import contextmanager
from copy import deepcopy
import json

from kravel.evidence import Progress, collect_evidence
from kravel.fixes import get_fix
from kravel.store import AuditStore
from kravel.workflows import contains_patch, recovery_observation, WorkflowManager
from kravel.config import load_config


class Tracer:
    trace_id = "test"
    @contextmanager
    def span(self, *_):
        yield self
    def set_outputs(self, *_):
        pass
    def set_content_inputs(self, *_):
        pass
    def set_content_outputs(self, *_):
        pass


class EvidenceKube:
    def __init__(self, missing=""):
        self.missing = missing
    def list_resources(self, kind, namespace, **_):
        assert namespace == "kravel-demo"
        if kind == self.missing:
            raise RuntimeError("permission denied")
        items = {"services": [{"kind": "Service", "metadata": {"name": "demo-gateway"}, "spec": {"selector": {"app": "wrong"}}}], "configmaps": [{"kind": "ConfigMap", "metadata": {"name": "config-demo"}, "data": {"MODE": "broken", "password": "hidden-secret"}}]}.get(kind, [])
        return {"items": items}
    def events(self, *_args, **_kwargs):
        return {"items": []}


def test_collector_partial_reads_are_gaps_and_evidence_is_sanitized():
    store = AuditStore()
    store.start_workflow("run", "investigation", "kravel-demo")
    bundle = collect_evidence(EvidenceKube("pods"), "kravel-demo", Progress(store, "run"), Tracer())
    assert "hidden-secret" not in json.dumps(bundle)
    assert bundle["gaps"]
    assert not any(f["resource"] == "Service/demo-gateway" for f in bundle["findings"])
    assert bundle["findings"][0]["evidenceIds"] == ["E4"]
    assert store.workflow("run")["steps"][0]["details"]["coverage"] == "unavailable"
    assert store.workflow("run")["steps"][0]["status"] == "unavailable"


def test_selector_diagnosis_is_grounded_and_network_probe_is_not_claimed():
    store = AuditStore()
    store.start_workflow("run", "investigation", "kravel-demo")
    bundle = collect_evidence(EvidenceKube(), "kravel-demo", Progress(store, "run"), Tracer(), "Service/demo-gateway")
    assert len(bundle["findings"]) == 1
    finding = bundle["findings"][0]
    assert finding["fixId"] == "fix_service_selector"
    assert "untested" in finding["uncertainty"]


def test_collector_accepts_api_list_items_without_kind():
    kube = EvidenceKube()
    original = kube.list_resources
    def no_kind(*args, **kwargs):
        result = original(*args, **kwargs)
        for item in result["items"]:
            item.pop("kind", None)
        return result
    kube.list_resources = no_kind
    store = AuditStore()
    store.start_workflow("run", "investigation", "kravel-demo")
    assert collect_evidence(kube, "kravel-demo", Progress(store, "run"), Tracer())["findings"]


def test_empty_endpoint_slice_null_is_not_an_exception():
    kube = EvidenceKube()
    original = kube.list_resources
    def null_endpoints(kind, *args, **kwargs):
        return {"items": [{"metadata": {"labels": {"kubernetes.io/service-name": "demo-gateway"}}, "endpoints": None}]} if kind == "endpointslices" else original(kind, *args, **kwargs)
    kube.list_resources = null_endpoints
    store = AuditStore()
    store.start_workflow("run", "investigation", "kravel-demo")
    result = collect_evidence(kube, "kravel-demo", Progress(store, "run"), Tracer(), "Service/demo-gateway")
    assert result["findings"][0]["fixId"] == "fix_service_selector"


def test_workflow_order_persistence_and_restart_does_not_replay(tmp_path):
    path = str(tmp_path/"runs.db")
    store = AuditStore(path)
    store.start_workflow("run", "investigation", "kravel-demo")
    store.workflow_step("run", "input", "Input guard", "completed", duration_ms=2)
    store.workflow_step("run", "model", "Qwen", "running")
    store.close()
    store = AuditStore(path)
    manager = WorkflowManager(object(), store, load_config())
    run = store.workflow("run")
    assert run["status"] == "interrupted"
    assert [s["step_key"] for s in run["steps"]] == ["input", "model"]
    assert run["steps"][1]["status"] == "interrupted"
    assert store.workflow_metrics()[0]["count"] == 1
    assert manager.model_slot.acquire(blocking=False)


def test_busy_model_rejects_second_investigation_without_unbounded_queue():
    manager = WorkflowManager(object(), AuditStore(), load_config())
    manager.model_slot.acquire()
    import pytest
    with pytest.raises(RuntimeError, match="already investigating"):
        manager.start("question", "kravel-demo")
    with pytest.raises(ValueError):
        manager.start("question", "production")


def test_recovery_requires_current_generation_owned_pods_and_identity():
    patch = get_fix("fix_image_pull", "kravel-demo")["operations"][0]["patch"]
    obj = {"metadata": {"uid": "deployment-uid", "generation": 2}, "spec": {"replicas": 1, **patch["spec"]}, "status": {"observedGeneration": 1, "replicas": 1, "updatedReplicas": 1, "availableReplicas": 1, "readyReplicas": 1}}
    rs = {"metadata": {"uid": "rs-new", "ownerReferences": [{"uid": "deployment-uid", "controller": True}]}, "spec": {"replicas": 1, "template": deepcopy(obj["spec"]["template"])}}
    pod = {"metadata": {"name": "image-pod", "uid": "pod-uid", "ownerReferences": [{"uid": "rs-old", "controller": True}]}, "status": {"phase": "Running", "containerStatuses": [{"ready": True, "state": {"running": {}}}]}}
    class Kube:
        def get_resource(self, *_): return {"object": deepcopy(obj)}
        def list_resources(self, kind, *_args, **_kwargs): return {"items": [rs] if kind == "replicasets" else [pod]}
    proposal = {"id": "proposal", "namespace": "kravel-demo", "fix_id": "fix_image_pull", "result": {"operations": [{"uid": "deployment-uid", "generation": 2}]}}
    assert recovery_observation(Kube(), proposal)[0] is False
    obj["status"]["observedGeneration"] = 2
    assert recovery_observation(Kube(), proposal)[0] is False  # old Ready Pod is not sufficient
    pod["metadata"]["ownerReferences"][0]["uid"] = "rs-new"
    assert recovery_observation(Kube(), proposal)[0] is True
    obj["metadata"]["uid"] = "replacement"
    assert recovery_observation(Kube(), proposal)[0] is False


def test_configmap_repair_restart_marker_is_unique_and_in_review_command():
    first = get_fix("fix_bad_configmap", "kravel-demo", "proposal-one")
    second = get_fix("fix_bad_configmap", "kravel-demo", "proposal-two")
    assert first["operations"] != second["operations"]
    assert "proposal-one" in first["command"]
    assert contains_patch({"containers": [{"name": "a", "extra": 1}]}, {"containers": [{"name": "a"}]})


def test_demo_session_boundary_persists_without_deleting_history(tmp_path):
    path = str(tmp_path / 'session.db')
    store = AuditStore(path)
    original = store.demo_session()
    store.start_workflow('old', 'investigation', 'kravel-demo')
    store.record('debugger', 'test-history')
    fresh = store.start_demo_session()
    assert fresh['id'] != original['id']
    assert store.workflow('old') and len(store.audit_entries()) == 1
    store.close()
    assert AuditStore(path).demo_session() == fresh


def test_recovery_checklist_requires_three_consecutive_ready_observations(monkeypatch):
    import kravel.workflows as workflows
    class LocalTracer(Tracer):
        def __init__(self, *_): pass
        def flush(self): pass
    class NoWait:
        def is_set(self): return False
        def wait(self, _): pass
    observations = iter([False, True, False, True, True, True])
    snapshots = []
    store = AuditStore()
    manager = WorkflowManager(object(), store, load_config())
    manager.stop = NoWait()
    manager.verifier_slots.acquire()
    store.start_workflow('verify:test', 'verification', 'kravel-demo')
    def observe(*_):
        snapshots.append({s['step_key']: s['status'] for s in store.workflow('verify:test')['steps']})
        return next(observations), {'reason': 'Test observation'}
    monkeypatch.setattr(workflows, 'MlflowTracer', LocalTracer)
    monkeypatch.setattr(workflows, 'recovery_observation', observe)
    manager._verify('verify:test', {'id': 'test', 'resource': 'Deployment/image-demo'})
    result = store.workflow('verify:test')
    steps = {s['step_key']: s['status'] for s in result['steps']}
    assert result['status'] == 'recovered'
    assert len(snapshots) == 6 and snapshots[0]['rollout'] == 'running'
    assert snapshots[2]['rollout'] == 'completed' and snapshots[2]['stability'] == 'running'
    assert steps['rollout'] == steps['stability'] == 'completed'
    assert result['payload']['stableObservations'] == 3


def test_interrupted_recovery_never_shows_success_checkmark(monkeypatch):
    import kravel.workflows as workflows
    class LocalTracer(Tracer):
        def __init__(self, *_): pass
        def flush(self): pass
    store = AuditStore()
    manager = WorkflowManager(object(), store, load_config())
    manager.verifier_slots.acquire()
    manager.stop.set()
    store.start_workflow('verify:test', 'verification', 'kravel-demo')
    monkeypatch.setattr(workflows, 'MlflowTracer', LocalTracer)
    monkeypatch.setattr(workflows, 'recovery_observation', lambda *_: (_ for _ in ()).throw(AssertionError('No reads after stop')))
    manager._verify('verify:test', {'id': 'test', 'resource': 'Deployment/image-demo'})
    result = store.workflow('verify:test')
    assert result['status'] == 'interrupted'
    assert all(s['status'] != 'completed' for s in result['steps'])
