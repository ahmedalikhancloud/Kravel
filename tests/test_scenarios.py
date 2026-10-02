from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from kravel.scenarios import catalog, by_id
from kravel.retrieval import retrieve, rrf, tokens
from kravel.remediation import make_profile, validate_document, enrollment_manifest
from kravel.drafts import draft_fix, resolve_proposal
from kravel.research import validate_url, Text
from kravel.broker import ApprovalBroker
from kravel.store import AuditStore
from kravel.evidence import collect_evidence, Progress
from kravel.workflows import recovery_observation
from kravel.diagnostics import event_matches
from test_workflows import Tracer
from test_api import serving, post
from kravel.api import create_server
from kravel.config import load_config


def daemonset():
    return {"kind": "DaemonSet", "metadata": {"name": "example-daemonset", "namespace": "kravel-demo", "uid": "ds-uid", "resourceVersion": "7", "generation": 2},
        "spec": {"template": {"spec": {"containers": [{"name": "fluentd", "image": "example/fluentd:reviewed"}]}}},
        "status": {"desiredNumberScheduled": 1, "currentNumberScheduled": 1, "updatedNumberScheduled": 1, "numberReady": 1, "numberAvailable": 1, "numberMisscheduled": 0, "observedGeneration": 2}}


def pod():
    return {"kind": "Pod", "metadata": {"name": "logger-node", "uid": "pod-uid", "ownerReferences": [{"kind": "DaemonSet", "name": "example-daemonset", "uid": "ds-uid", "controller": True}]},
        "spec": {"containers": [{"name": "fluentd", "image": "example/fluentd:reviewed"}]},
        "status": {"phase": "Pending", "containerStatuses": [{"name": "fluentd", "ready": False, "state": {"waiting": {"reason": "ImagePullBackOff"}}}]}}


def enroll(tmp_path, monkeypatch):
    profile = make_profile(daemonset(), "legacy_image_format", container="fluentd")
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({"version": 1, "profiles": [profile]}))
    monkeypatch.setenv("KRAVEL_REPAIR_PROFILES_PATH", str(path))
    return profile, path


def draft():
    return {"kind": "daemonsets", "name": "example-daemonset", "patch": {"spec": {"template": {"spec": {"containers": [{"name": "fluentd", "image": "example/fluentd:new-operator-reviewed"}]}}}}, "rationale": "E1 shows an unsupported image format; the operator supplied a reviewed compatible replacement.", "evidenceIds": ["E1"]}


@pytest.fixture(autouse=True)
def isolate_local_configuration(monkeypatch):
    for name in ("KRAVEL_REPAIR_PROFILES_PATH", "KRAVEL_RUNBOOKS_PATH", "KRAVEL_RAG_EMBEDDING_PATH", "KRAVEL_RAG_RERANKER_PATH"):
        monkeypatch.delenv(name, raising=False)


def test_catalog_has_25_plus_25_distinct_evidence_and_verification_contracts():
    cases = catalog()
    assert len(cases) == len({c["id"] for c in cases}) == 50
    assert sum(c["group"] == "common" for c in cases) == 25
    assert sum(c["group"] == "difficult" for c in cases) == 25
    assert all(c["evidenceRequired"] and c["remediation"] and c["verification"] and c["source"].startswith("https://kubernetes.io/docs/") for c in cases)
    assert by_id("etcd_latency")["executionMode"] == "operator_led"


@pytest.mark.parametrize("case", catalog(), ids=lambda c: c["id"])
def test_every_runbook_is_retrievable_by_its_title(case):
    assert case["id"] in [h["id"] for h in retrieve(case["title"], limit=6)["hits"]]


def test_exact_runtime_format_error_ranks_legacy_image_first():
    result = retrieve("media type application/vnd.docker.distribution.manifest.v1+prettyjws is no longer supported")
    assert result["hits"][0]["id"] == "legacy_image_format"
    assert result["mode"] == "bm25" and not result["reranked"]
    assert "permission" in result["notice"]
    assert "imagepullbackoff" in tokens("ImagePullBackOff")


def test_hybrid_rrf_rerank_and_cache_do_not_store_operator_queries(tmp_path):
    calls = []
    def encode(texts):
        calls.append(len(texts))
        return [[1.0, 1.0 if "image" in t.lower() else 0.0] for t in texts]
    path = str(tmp_path / "vectors.db")
    result = retrieve("prettyjws", encoder=encode, reranker=lambda pairs: list(range(len(pairs))), cache_path=path)
    assert result["mode"] == "hybrid_rrf" and result["reranked"]
    assert {"bm25Ms", "denseMs", "rrfMs", "cross_encoderMs"} <= result["timings"].keys()
    retrieve("another private operator question", encoder=encode, cache_path=path)
    assert calls.count(50) == 1
    assert b"private operator question" not in (tmp_path / "vectors.db").read_bytes()
    assert rrf([[('a', 7), ('b', 5)], [('b', 8), ('a', 3)]])[0][1] == pytest.approx(1/61 + 1/62)


def test_optional_model_failure_falls_back_honestly(monkeypatch):
    monkeypatch.setenv("KRAVEL_RAG_EMBEDDING_PATH", "/nonexistent/model")
    result = retrieve("OOMKilled")
    assert result["mode"] == "bm25" and result["unavailable"][0]["stage"] == "dense"
    assert result["hits"]


def test_custom_runbook_adds_knowledge_but_not_write_capabilities(tmp_path, monkeypatch):
    case = {k: catalog()[0][k] for k in ("id", "title", "category", "signals", "evidenceRequired", "remediation", "verification", "source")}
    case["id"], case["title"] = "team-specific", "Our application-specific memory runbook"
    path = tmp_path / "runbooks.json"; path.write_text(json.dumps([case]))
    monkeypatch.setenv("KRAVEL_RUNBOOKS_PATH", str(path))
    assert len(catalog()) == 51 and by_id("team-specific")["restorableFields"] == []
    with pytest.raises(ValueError, match="operator-led"):
        make_profile(daemonset(), "team-specific")


def test_enrollment_generates_only_named_broker_rbac(tmp_path, monkeypatch):
    profile, _ = enroll(tmp_path, monkeypatch)
    manifest = enrollment_manifest([profile])
    role = manifest["items"][1]
    assert role["rules"] == [{"apiGroups": ["apps"], "resources": ["daemonsets"], "resourceNames": ["example-daemonset"], "verbs": ["get", "patch"]}]
    assert manifest["items"][2]["subjects"][0]["name"] == "kravel-approval-broker"


@pytest.mark.parametrize("change", ["namespace", "secret", "hostNetwork", "serviceAccountName", "privileged", "directive", "delete", "metadata", "container"])
def test_novel_repair_rejects_scope_and_privilege_expansion(tmp_path, monkeypatch, change):
    enroll(tmp_path, monkeypatch)
    value = draft(); namespace = "kravel-demo"
    spec = value["patch"]["spec"]["template"]["spec"]
    if change == "namespace": namespace = "production"
    elif change == "secret": value["kind"] = "secrets"
    elif change in {"hostNetwork", "serviceAccountName"}: spec[change] = True
    elif change == "privileged": spec["containers"][0]["securityContext"] = {"privileged": True}
    elif change == "directive": spec["containers"][0]["$patch"] = "replace"
    elif change == "delete": spec["containers"][0]["image"] = None
    elif change == "metadata": value["patch"]["metadata"] = {"uid": "forged"}
    elif change == "container": spec["containers"][0]["name"] = "new-sidecar"
    with pytest.raises(ValueError): draft_fix(value, namespace)


def test_unseen_repair_values_are_supported_but_unenrolled_target_is_not(tmp_path, monkeypatch):
    idea = draft_fix(draft(), "kravel-demo", require_authority=False)
    assert not idea["eligible"]
    with pytest.raises(ValueError, match="enrollment"):
        draft_fix(draft(), "kravel-demo")
    enroll(tmp_path, monkeypatch)
    idea = draft_fix(draft(), "kravel-demo")
    assert idea["eligible"] and "new-operator-reviewed" in idea["command"]
    assert idea["id"].startswith("draft-")


def test_container_field_permissions_do_not_transfer_to_other_enrolled_containers(tmp_path, monkeypatch):
    profile, path = enroll(tmp_path, monkeypatch)
    memory_profile = {**profile, "id": "profile-memory", "scenarioId": "oomkilled",
        "patch": {"spec": {"template": {"spec": {"containers": [{"name": "sidecar", "resources": {"limits": {"memory": "128Mi"}}}]}}}}}
    path.write_text(json.dumps({"version": 1, "profiles": [profile, memory_profile]}))
    value = draft(); value["patch"]["spec"]["template"]["spec"]["containers"][0]["name"] = "sidecar"
    with pytest.raises(ValueError, match="container"):
        draft_fix(value, "kravel-demo")


def test_novel_configmap_keys_require_explicit_operator_enrollment():
    value = {"kind": "configmaps", "name": "config-demo", "patch": {"data": {"NEW_KEY": "new-value"}},
        "rationale": "Review an independently established configuration value.", "evidenceIds": ["E4"]}
    with pytest.raises(ValueError, match="data keys"):
        draft_fix(value, "kravel-demo")


@pytest.mark.parametrize("url", ["http://kubernetes.io/docs/", "https://127.0.0.1/docs/", "https://evil.example/docs/", "https://kubernetes.io:444/docs/", "https://user:pass@kubernetes.io/docs/", "https://kubernetes.io/docs/?token=private", "https://kubernetes.io/docs/%2e%2e/", "https://kubernetes.io/docs/../api", "https://kubernetes.io/other/"])
def test_research_blocks_private_credentialed_and_unapproved_sources(url):
    with pytest.raises(ValueError): validate_url(url)


def test_research_strips_scripts_and_prefers_main_article():
    validate_url("https://kubernetes.io/docs/concepts/containers/images/")
    parser = Text(); parser.feed("<nav>menu</nav><main><h1>Images</h1><script>malicious</script><p>Manifest formats</p></main>")
    assert parser.main_parts == ["Images", "Manifest formats"]


def test_daemonset_target_retains_uid_owned_pod_findings_and_exact_events(tmp_path, monkeypatch):
    profile, _ = enroll(tmp_path, monkeypatch)
    event = {"reason": "Failed", "involvedObject": {"name": "logger-node", "uid": "pod-uid"}, "message": 'Failed to pull image: media type "prettyjws" is no longer supported'}
    class Kube:
        def list_resources(self, kind, *_args, **_kwargs): return {"items": {"pods": [pod()], "daemonsets": [daemonset()]}.get(kind, [])}
        def events(self, *_args, **_kwargs): return {"items": [event]}
        def pod_logs(self, *_): raise RuntimeError("Container never started")
    store = AuditStore(); store.start_workflow("run", "investigation", "kravel-demo")
    bundle = collect_evidence(Kube(), "kravel-demo", Progress(store, "run"), Tracer(), "DaemonSet/example-daemonset")
    finding = bundle["findings"][0]
    assert finding["resource"] == "Pod/logger-node" and finding["fixId"] == profile["id"]
    assert finding["scenarioIds"] == ["legacy_image_format"]
    assert all(eid in {e["id"] for e in bundle["evidence"]} for eid in finding["evidenceIds"])
    event["involvedObject"]["uid"] = "replaced-pod"
    assert not event_matches(pod(), [event])


class MutableKube:
    def __init__(self): self.obj, self.calls = daemonset(), []
    def get_resource(self, *_): return {"object": deepcopy(self.obj)}
    def patch(self, kind, name, namespace, patch, *, content_type, dry_run):
        self.calls.append(dry_run)
        result = deepcopy(self.obj); result["spec"] = deepcopy(patch["spec"])
        if not dry_run: self.obj = result
        return {"object": result, "dryRun": dry_run}
    def list_resources(self, *_args, **_kwargs):
        p = pod(); p["spec"] = deepcopy(self.obj["spec"]["template"]["spec"])
        p["status"] = {"phase": "Running", "containerStatuses": [{"ready": True, "state": {"running": {}}}]}
        return {"items": [p]}


def broker_config(): return SimpleNamespace(slack_bot_token="", slack_channel_id="", approval_timeout_seconds=300)


def test_novel_draft_requires_dry_run_human_approval_and_current_daemonset_recovery(tmp_path, monkeypatch):
    enroll(tmp_path, monkeypatch)
    monkeypatch.setattr(ApprovalBroker, "_start_waiter", lambda *_: None)
    kube, store = MutableKube(), AuditStore(); broker = ApprovalBroker(kube, store, broker_config())
    proposal = broker.create_draft(draft(), "kravel-demo")
    assert kube.calls == [True] and proposal["status"] == "pending"
    assert broker._execute(proposal) is None and kube.calls == [True]
    reviewed = resolve_proposal(proposal)
    assert reviewed["operations"][0]["patch"] == draft()["patch"]
    broker._execute(broker.approve(proposal["id"], "human"))
    proposal = store.proposal(proposal["id"])
    assert kube.calls == [True, False] and proposal["status"] == "executed"
    assert recovery_observation(kube, proposal)[0]
    kube.obj["status"]["updatedNumberScheduled"] = 0
    assert not recovery_observation(kube, proposal)[0]


def test_profile_revocation_before_execution_invalidates_novel_approval(tmp_path, monkeypatch):
    _, path = enroll(tmp_path, monkeypatch)
    monkeypatch.setattr(ApprovalBroker, "_start_waiter", lambda *_: None)
    kube, store = MutableKube(), AuditStore(); broker = ApprovalBroker(kube, store, broker_config())
    proposal = broker.create_draft(draft(), "kravel-demo")
    path.write_text(json.dumps({"version": 1, "profiles": []}))
    broker._execute(broker.approve(proposal["id"], "human"))
    assert store.proposal(proposal["id"])["status"] == "failed" and kube.calls == [True]


def test_operator_led_case_cannot_be_enrolled_as_an_executable_profile():
    with pytest.raises(ValueError, match="operator-led"):
        make_profile(daemonset(), "etcd_latency")


def test_custom_profile_identity_is_strict():
    with pytest.raises(ValueError): validate_document({"version": 1, "profiles": [{"id": "whatever"}]})


@pytest.mark.parametrize("value", [{"$patch": "replace"}, {"limits": {"memory": None}}, {"limits": {"$retainKeys": ["memory"]}}])
def test_nested_profile_directives_and_deletions_cannot_bypass_field_policy(value):
    profile = {
        "id": "profile-resources", "title": "Reviewed memory repair", "namespace": "kravel-demo",
        "kind": "daemonsets", "name": "example-daemonset", "scenarioId": "oomkilled",
        "patch": {"spec": {"template": {"spec": {"containers": [{"name": "fluentd", "resources": value}]}}}},
    }
    with pytest.raises(ValueError): validate_document({"version": 1, "profiles": [profile]})


def test_public_library_api_has_exact_counts_and_no_agent_approval_route():
    import urllib.request
    config = load_config(); config.host, config.port = "127.0.0.1", 0
    with serving(create_server(AuditStore(), config, object())) as base:
        with urllib.request.urlopen(base + "/v1/runbooks") as response:
            result = json.load(response)
        assert result["counts"] == {"common": 25, "difficult": 25}
        with urllib.request.urlopen(base + "/v1/runbooks/search?q=prettyjws") as response:
            assert json.load(response)["hits"][0]["id"] == "legacy_image_format"
        assert post(base, "/v1/proposals/id/approve", {})[0] == 404


def test_api_forwards_only_a_saved_permitted_run_draft(monkeypatch):
    import kravel.api as api
    store = AuditStore(); config = load_config(); config.host, config.port = "127.0.0.1", 0
    store.start_workflow("run", "investigation", "kravel-demo")
    idea = draft_fix(draft(), "kravel-demo", require_authority=False)
    store.update_workflow("run", status="completed", payload={"draftRepairs": [idea], "disposition": "success"})
    forwarded = []
    def broker_request(_config, _path, _method, body):
        forwarded.append(body)
        return {"id": "proposal", "resource": idea["resource"], "status": "pending"}
    monkeypatch.setattr(api, "_broker_request", broker_request)
    with serving(create_server(store, config, object())) as base:
        # create_server marks only running workflows interrupted; the saved run stays completed.
        assert post(base, "/v1/proposals", {"runId": "run", "draftId": "invented"})[0] == 400
        assert post(base, "/v1/proposals", {"runId": "run", "draftId": idea["id"]})[0] == 201
        assert forwarded[0]["draft"] == idea["draft"]
        store.update_workflow("run", payload={"disposition": "blocked"})
        assert post(base, "/v1/proposals", {"runId": "run", "draftId": idea["id"]})[0] == 400


def test_langgraph_stages_a_novel_plan_and_traces_retrieval_without_submitting_approval(monkeypatch):
    import kravel.debugger as debugger
    from test_debugger import FakeTracer, response, call
    tracer = FakeTracer(); completions = []
    def complete(**kwargs):
        completions.append(kwargs)
        if len(completions) == 1:
            return response(None, [call("draft_repair", json.dumps(draft()), "new-plan")])
        return response("Finding: the Pod cannot pull its image. Evidence: E1 and pull Events. Uncertainty: the replacement requires operator review. Suggested next step: inspect the staged draft; no change has been executed. Prevention: validate image formats before delivery.")
    monkeypatch.setattr(debugger, "OpenAI", lambda **_: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=complete))))
    monkeypatch.setattr(debugger, "MlflowTracer", lambda *_: tracer)
    def policy_check(self, value, phase, **_kwargs):
        return {"phase": phase, "decision": "allow", "reasonCode": "policy_passed", "reason": "Test policy passed", "flags": {}, "checks": [], "requestMode": "investigation", "framework": "test", "policyVersion": "test", "latencyMs": 0.0}
    monkeypatch.setattr(debugger.SemanticGuardrails, "check", policy_check)
    class Kube:
        def list_resources(self, kind, *_args, **_kwargs): return {"items": {"pods": [pod()], "daemonsets": [daemonset()]}.get(kind, [])}
        def events(self, *_args, **_kwargs): return {"items": []}
        def get_resource(self, *_args): raise RuntimeError("ConfigMap demo absent")
        def pod_logs(self, *_args): raise RuntimeError("Image never started")
    store = AuditStore(); store.start_workflow("run", "investigation", "kravel-demo")
    result = debugger.run_debugger(Kube(), store, load_config(), "Investigate my DaemonSet image failure and draft a repair for review", "kravel-demo", run_id="run", progress=Progress(store, "run"), target="DaemonSet/example-daemonset")
    assert result["draftRepairs"] and not result["draftRepairs"][0]["eligible"]
    assert result["mutationExecuted"] is False and not store.active_proposals()
    assert {"rag.bm25", "rag.context", "tool.draft_repair", "tool.authorization"} <= set(tracer.names)
    assert result["runbooks"]["hits"] and result["timings"]["retrievalMs"] > 0
    assert not any("draft_repair" in e["label"] for e in result["evidence"])


def test_unreviewed_reference_path_cannot_carry_data_to_an_approved_host():
    with pytest.raises(ValueError, match="pre-reviewed"):
        validate_url("https://kubernetes.io/docs/private-cluster-name/")


def test_research_rejects_private_dns_and_pins_public_tls(monkeypatch):
    import kravel.research as research
    monkeypatch.setattr(research.socket, "getaddrinfo", lambda *_args, **_kwargs: [(0, 0, 0, "", ("127.0.0.1", 443))])
    with pytest.raises(ValueError, match="public IP"):
        research.fetch_reference("https://kubernetes.io/docs/concepts/containers/images/")


def test_live_readiness_events_do_not_masquerade_as_registry_connectivity():
    event = {"reason": "Unhealthy", "involvedObject": {"name": "logger-node", "uid": "pod-uid"}, "message": "Readiness probe failed: dial tcp connection refused"}
    assert event_matches(pod(), [event]) == {"readiness_failure"}


def test_shared_service_account_ca_does_not_expand_target_log_scope():
    selected, unrelated = pod(), pod()
    unrelated["metadata"] = {"name": "other-app", "uid": "other-uid"}
    for item in (selected, unrelated):
        item["spec"]["volumes"] = [{"projected": {"sources": [{"configMap": {"name": "kube-root-ca.crt"}}]}}]
    reads = []
    class Kube:
        def list_resources(self, kind, *_args, **_kwargs):
            return {"items": {"pods": [unrelated, selected], "daemonsets": [daemonset()], "configmaps": [{"metadata": {"name": "kube-root-ca.crt"}, "data": {"ca.crt": "public CA"}}]}.get(kind, [])}
        def events(self, *_args, **_kwargs): return {"items": []}
        def pod_logs(self, name, *_args): reads.append(name); return {"logs": "bounded log"}
    store = AuditStore(); store.start_workflow("run", "investigation", "kravel-demo")
    bundle = collect_evidence(Kube(), "kravel-demo", Progress(store, "run"), Tracer(), "DaemonSet/example-daemonset")
    assert reads == [selected["metadata"]["name"]]
    assert all(f["resource"] != "Pod/other-app" for f in bundle["findings"])


def test_spoofed_demo_label_neither_selects_demo_fix_nor_marks_other_deployment_broken():
    from kravel.tools import discover_issues
    selected = pod(); selected["metadata"]["labels"] = {"app": "image-demo"}
    deployment = {"kind": "Deployment", "metadata": {"name": "image-demo", "uid": "real-image-uid"}, "spec": {"replicas": 1}, "status": {"availableReplicas": 1}}
    class Kube:
        def list_resources(self, kind, *_args, **_kwargs):
            return {"items": {"pods": [selected], "daemonsets": [daemonset()], "deployments": [deployment]}.get(kind, [])}
        def events(self, *_args, **_kwargs): return {"items": []}
        def get_resource(self, *_args): raise ValueError("Not installed")
    snapshot = discover_issues(Kube(), "kravel-demo")
    assert snapshot["issues"][0]["fixId"] == ""
    assert next(r for r in snapshot["resources"] if r["kind"] == "Deployment")["health"] == "healthy"
