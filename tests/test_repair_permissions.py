"""Write-capable agent boundary: dry-run -> human decision -> exact patch."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

import kravel.debugger as debugger
from kravel.api import create_server
from kravel.broker import ApprovalBroker
from kravel.config import load_config
from kravel.drafts import draft_fix
from kravel.evidence import Progress
from kravel.guardrails import guard_debugger_output
from kravel.investigation_tools import authorize_tool, INVESTIGATION_TOOLS
from kravel.store import AuditStore
from test_api import serving
from test_debugger import FakeTracer, call, response
from test_scenarios import daemonset, pod, draft, MutableKube, broker_config


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.delenv("KRAVEL_REPAIR_MODE", raising=False)
    monkeypatch.delenv("KRAVEL_REPAIR_PROFILES_PATH", raising=False)
    monkeypatch.setattr(ApprovalBroker, "_start_waiter", lambda *_: None)


@pytest.mark.parametrize("kind", ["deployments", "daemonsets", "configmaps", "services"])
def test_default_mode_allows_new_named_resources_but_not_execution(kind):
    value = draft(); value["kind"], value["name"] = kind, "created-by-human"
    if kind == "configmaps": value["patch"] = {"data": {"REVIEWED_TIMEOUT": "30"}}
    if kind == "services": value["patch"] = {"spec": {"selector": {"app": "reviewed-backend"}}}
    idea = draft_fix(value, "kravel-demo")
    assert idea["eligible"] and "no resource enrollment" in idea["authorizationReason"]


def test_unknown_mode_fails_closed_at_startup_and_at_proposal(monkeypatch):
    monkeypatch.setenv("KRAVEL_REPAIR_MODE", "everything")
    with pytest.raises(ValueError, match="KRAVEL_REPAIR_MODE"): load_config()
    with pytest.raises(ValueError, match="KRAVEL_REPAIR_MODE"): draft_fix(draft(), "kravel-demo")


def test_unverified_latest_image_advice_cannot_pass_clean_output_policy():
    guarded = guard_debugger_output("Finding: cannot pull image. Uncertainty: replacement unknown.\nUse fluentd:latest as the replacement.")
    assert "fluentd:latest" not in guarded["value"]
    assert guarded["decision"] != "allow"
    assert any(f["code"] == "latest_image_advice_withheld" for f in guarded["findings"])


def test_new_container_is_rejected_by_live_executor_before_dry_run():
    kube, store = MutableKube(), AuditStore()
    value = draft(); value["patch"]["spec"]["template"]["spec"]["containers"][0]["name"] = "new-sidecar"
    with pytest.raises(ValueError, match="unreviewed container"):
        ApprovalBroker(kube, store, broker_config()).create_draft(value, "kravel-demo")
    assert kube.calls == [] and not store.active_proposals()


def test_live_findings_offer_approval_gated_repair_instead_of_stale_enrollment_message():
    from kravel.diagnostics import enrich_findings
    finding = {"resource": "Pod/logger-node", "cause": "ImagePullBackOff", "fixId": ""}
    result = enrich_findings([finding], {"pods": [pod()], "daemonsets": [daemonset()]}, [], "kravel-demo")
    assert "no resource enrollment" in result[0]["repairAvailability"]


@pytest.mark.parametrize("decision", ["pending", "rejected", "expired"])
def test_novel_unenrolled_repair_cannot_execute_without_timely_human_approval(decision):
    kube, store = MutableKube(), AuditStore(); broker = ApprovalBroker(kube, store, broker_config())
    if decision == "expired": broker.config.approval_timeout_seconds = -1
    proposal = broker.create_draft(draft(), "kravel-demo")
    if decision == "rejected": broker.reject(proposal["id"], "test-human")
    if decision == "expired": broker.approve(proposal["id"], "late-test-human")
    assert broker._execute(proposal) is None and kube.calls == [True]
    assert store.proposal(proposal["id"])["status"] == decision


def test_policy_tightening_invalidates_already_reviewed_novel_plan(monkeypatch):
    kube, store = MutableKube(), AuditStore(); broker = ApprovalBroker(kube, store, broker_config())
    proposal = broker.create_draft(draft(), "kravel-demo")
    monkeypatch.setenv("KRAVEL_REPAIR_MODE", "enrolled_only")
    broker._execute(broker.approve(proposal["id"], "test-human"))
    assert kube.calls == [True] and store.proposal(proposal["id"])["status"] == "failed"


@pytest.mark.parametrize("args", [{}, {"fixId": "fix_image_pull", "draftId": "draft-id"}, {"command": "kubectl patch"}, {"fixId": 1}])
def test_review_tool_rejects_ambiguous_or_arbitrary_arguments(args):
    with pytest.raises(ValueError): authorize_tool("request_repair_approval", args, "kravel-demo")
    assert not any(t["function"]["name"] in {"approve", "patch", "execute", "shell"} for t in INVESTIGATION_TOOLS)


@pytest.mark.parametrize("blocked,request_fix,unknown_id", [(False, True, False), (True, True, False), (False, False, False), (False, True, True)])
def test_graph_submits_only_current_evidence_linked_plan_after_output_guards(monkeypatch, blocked, request_fix, unknown_id):
    tracer, completions, submissions = FakeTracer(), [], []
    plan_id = draft_fix(draft(), "kravel-demo")["id"]
    def complete(**kwargs):
        completions.append(kwargs)
        if len(completions) == 1: return response(None, [call("draft_repair", json.dumps(draft()), "draft")])
        if len(completions) == 2: return response(None, [call("request_repair_approval", json.dumps({"draftId": "draft-other" if unknown_id else plan_id}), "review")])
        return response("Finding: the Pod cannot pull its image. Evidence: E1. Uncertainty: application recovery is not verified. Suggested next step: review the repair draft; no change was executed. Prevention: validate image compatibility.")
    def check(self, _value, phase, **_):
        reject = blocked and phase == "output"
        return {"decision": "reject" if reject else "allow", "phase": phase, "reasonCode": "test", "reason": "Test", "checks": [], "requestMode": "investigation", "latencyMs": 0, "framework": "test", "policyVersion": "test"}
    def submit(_config, payload):
        assert "guardrail.output" in tracer.names and not blocked
        submissions.append(deepcopy(payload))
        return {"id": "pending-review", "resource": "DaemonSet/example-daemonset", "status": "pending"}
    monkeypatch.setattr(debugger, "request_approval", submit)
    monkeypatch.setattr(debugger, "OpenAI", lambda **_: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=complete))))
    monkeypatch.setattr(debugger, "MlflowTracer", lambda *_: tracer)
    monkeypatch.setattr(debugger.SemanticGuardrails, "check", check)
    class Kube:
        def list_resources(self, kind, *_args, **_): return {"items": {"pods": [pod()], "daemonsets": [daemonset()]}.get(kind, [])}
        def events(self, *_args, **_): return {"items": []}
        def pod_logs(self, *_): raise ValueError("Image never started")
        def get_resource(self, *_): raise ValueError("Absent demo ConfigMap")
    store = AuditStore(); store.start_workflow("run", "investigation", "kravel-demo")
    question = "Fix my DaemonSet image failure and request approval" if request_fix else "Investigate the DaemonSet image failure"
    result = debugger.run_debugger(Kube(), store, load_config(), question, "kravel-demo", run_id="run", progress=Progress(store, "run"), target="DaemonSet/example-daemonset")
    assert result["mutationExecuted"] is False
    allowed = not blocked and request_fix and not unknown_id
    assert bool(submissions) == allowed and bool(result["approvalRequests"]) == allowed
    if allowed:
        assert submissions[0]["draft"] == draft()
        assert "repair.request_approval" in tracer.names
        assert result["reviewStatus"] == "human_review_requested"
    assert not any("request_repair_approval" in e["label"] for e in result["evidence"])
