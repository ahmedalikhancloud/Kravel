from types import SimpleNamespace
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor

import pytest

from kravel.broker import ApprovalBroker
from kravel.fixes import get_fix
from kravel.store import AuditStore


class FakeKube:
    def __init__(self):
        self.calls = []
        self.objects = {}

    def get_resource(self, kind, name, namespace):
        key = (kind, name, namespace)
        if key not in self.objects:
            self.objects[key] = {"kind": "ConfigMap" if kind == "configmaps" else "Deployment", "metadata": {"name": name, "uid": f"uid-{name}", "resourceVersion": "7"}, **({"data": {"MODE": "broken"}} if kind == "configmaps" else {"spec": {"replicas": 1}})}
        return {"object": deepcopy(self.objects[key])}

    def patch(self, kind, name, namespace, patch, *, content_type, dry_run):
        self.calls.append({"kind": kind, "name": name, "namespace": namespace, "patch": patch, "contentType": content_type, "dryRun": dry_run})
        object_kind = "ConfigMap" if kind == "configmaps" else "Deployment"
        result = {"kind": object_kind, "metadata": {"name": name, "resourceVersion": "7", "generation": 2}, **({"data": patch.get("data", {})} if kind == "configmaps" else {"spec": patch.get("spec", {})})}
        if not dry_run:
            self.objects[(kind, name, namespace)].update(deepcopy(patch))
        return {"object": result, "dryRun": dry_run, "durationMs": 3}


def config():
    return SimpleNamespace(slack_bot_token="", slack_channel_id="", approval_timeout_seconds=300)


def test_broker_dry_runs_then_executes_only_after_approval():
    kube, store = FakeKube(), AuditStore()
    broker = ApprovalBroker(kube, store, config())
    broker._wait = lambda _proposal_id: None

    proposal = broker.create("fix_image_pull", "kravel-demo")
    assert proposal["status"] == "pending"
    assert kube.calls and all(call["dryRun"] for call in kube.calls)

    approved = broker.approve(proposal["id"], "local-human")
    assert approved["status"] == "approved"
    broker._execute(approved)
    assert store.proposal(proposal["id"])["status"] == "executed"
    assert any(not call["dryRun"] for call in kube.calls)
    assert all(call["patch"]["metadata"]["uid"] == "uid-image-demo" for call in kube.calls)
    broker._execute(approved)
    assert sum(not call["dryRun"] for call in kube.calls) == 1


def test_fix_catalog_rejects_other_namespaces_and_arbitrary_commands():
    with pytest.raises(ValueError):
        get_fix("fix_image_pull", "default")
    with pytest.raises(ValueError):
        get_fix("kubectl delete namespace production", "kravel-demo")


def make_broker(monkeypatch):
    monkeypatch.setattr(ApprovalBroker, "_start_waiter", lambda *_: None)
    kube, store = FakeKube(), AuditStore()
    return ApprovalBroker(kube, store, config()), kube, store


def test_unapproved_proposal_cannot_execute(monkeypatch):
    broker, kube, store = make_broker(monkeypatch)
    proposal = broker.create("fix_crash_loop", "kravel-demo")
    assert broker._execute(proposal) is None
    assert all(call["dryRun"] for call in kube.calls)
    assert store.proposal(proposal["id"])["status"] == "pending"


@pytest.mark.parametrize("change", ["reset", "spec"])
def test_resource_changes_invalidate_review(monkeypatch, change):
    broker, kube, store = make_broker(monkeypatch)
    proposal = broker.create("fix_image_pull", "kravel-demo")
    obj = kube.objects[("deployments", "image-demo", "kravel-demo")]
    if change == "reset":
        obj["metadata"]["uid"] = "replacement-uid"
    else:
        obj["spec"]["replicas"] = 2
    broker._execute(broker.approve(proposal["id"], "human"))
    assert store.proposal(proposal["id"])["status"] == "failed"
    assert all(call["dryRun"] for call in kube.calls)


def test_timeout_and_restart_fail_closed(monkeypatch):
    broker, kube, store = make_broker(monkeypatch)
    broker.config.approval_timeout_seconds = -1
    proposal = broker.create("fix_oom_memory", "kravel-demo")
    assert broker.approve(proposal["id"], "late-human")["status"] == "expired"
    assert all(call["dryRun"] for call in kube.calls)
    broker.config.approval_timeout_seconds = 300
    proposal = broker.create("fix_oom_memory", "kravel-demo")
    broker.approve(proposal["id"], "human")
    ApprovalBroker(kube, store, config())
    assert store.proposal(proposal["id"])["status"] == "failed"
    assert all(call["dryRun"] for call in kube.calls)


def test_pending_proposal_resumes_expiration_after_restart(monkeypatch):
    broker, kube, store = make_broker(monkeypatch)
    proposal = broker.create("fix_image_pull", "kravel-demo")
    resumed = []
    monkeypatch.setattr(ApprovalBroker, "_start_waiter", lambda self, proposal_id: resumed.append(proposal_id))
    ApprovalBroker(kube, store, config())
    assert resumed == [proposal["id"]]


def test_concurrent_approval_is_attributed_once_and_execution_is_once(monkeypatch):
    broker, kube, store = make_broker(monkeypatch)
    proposal = broker.create("fix_crash_loop", "kravel-demo")
    with ThreadPoolExecutor(max_workers=4) as pool:
        approvals = list(pool.map(lambda n: broker.approve(proposal["id"], f"human-{n}"), range(4)))
        list(pool.map(broker._execute, approvals))
    assert sum(not call["dryRun"] for call in kube.calls) == 1
    assert sum(item["action"] == "proposal.approved" for item in store.audit_entries()) == 1


def test_review_command_is_derived_from_all_operations():
    import json
    import shlex

    for fix_id in ("fix_oom_memory", "fix_image_pull", "fix_crash_loop", "fix_bad_configmap"):
        fix = get_fix(fix_id, "kravel-demo")
        commands = fix["command"].split(" && ")
        assert len(commands) == len(fix["operations"])
        for command, operation in zip(commands, fix["operations"]):
            args = shlex.split(command)
            assert json.loads(args[args.index("-p") + 1]) == operation["patch"]


def test_two_operation_config_fix_records_partial_failure_without_replay(monkeypatch):
    broker, kube, store = make_broker(monkeypatch)
    original = kube.patch
    def fail_second(kind, *args, **kwargs):
        if kind == "deployments" and not kwargs["dry_run"]:
            raise RuntimeError("simulated version conflict")
        return original(kind, *args, **kwargs)
    kube.patch = fail_second
    proposal = broker.create("fix_bad_configmap", "kravel-demo")
    broker._execute(broker.approve(proposal["id"], "human"))
    result = store.proposal(proposal["id"])
    assert result["status"] == "failed" and len(result["result"]["operations"]) == 1
    workflow = store.workflow(proposal["id"])
    assert workflow["status"] == "partially_failed"
    states = {s["step_key"]: s["status"] for s in workflow["steps"]}
    assert states["apply_1"] == "completed" and states["apply_2"] == "failed"
    broker._execute(result)
    assert sum(not call["dryRun"] for call in kube.calls) == 1
