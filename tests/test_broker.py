from types import SimpleNamespace

import pytest

from kravel.broker import ApprovalBroker
from kravel.fixes import get_fix
from kravel.store import AuditStore


class FakeKube:
    def __init__(self):
        self.calls = []

    def patch(self, kind, name, namespace, patch, *, content_type, dry_run):
        self.calls.append({"kind": kind, "name": name, "namespace": namespace, "patch": patch, "contentType": content_type, "dryRun": dry_run})
        object_kind = "ConfigMap" if kind == "configmaps" else "Deployment"
        return {"object": {"kind": object_kind, "metadata": {"name": name, "resourceVersion": "7", "generation": 2}, **({"data": patch.get("data", {})} if kind == "configmaps" else {"spec": patch.get("spec", {})})}, "dryRun": dry_run, "durationMs": 3}


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


def test_fix_catalog_rejects_other_namespaces_and_arbitrary_commands():
    with pytest.raises(ValueError):
        get_fix("fix_image_pull", "default")
    with pytest.raises(ValueError):
        get_fix("kubectl delete namespace production", "kravel-demo")
