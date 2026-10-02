import json
from types import SimpleNamespace
from copy import deepcopy

import pytest

from kravel.broker import ApprovalBroker
from kravel.cluster_plans import canonical_plan, validate_argv, safe_output, KubectlExecutor
from kravel.config import KubeConfig
from kravel.store import AuditStore
from kravel.model_routing import select_model
from kravel.guardrails import guard_request_scope


def plan():
    return {"title": "Create a new kind", "summary": "Create desired state, then verify it. No observed fault is asserted.", "files": {"quota.yaml": "apiVersion: v1\nkind: ResourceQuota\nmetadata:\n  name: planned-quota\nspec:\n  hard:\n    pods: '20'\n"}, "steps": [{"label": "Apply quota", "argv": ["apply", "-f", "quota.yaml", "-n", "default"]}, {"label": "Verify quota", "argv": ["get", "resourcequota/planned-quota", "-n", "default", "-o", "json"]}]}


class Runner:
    def __init__(self): self.calls, self.fail_actual, self.fail_preview = [], False, False
    def __call__(self, argv, files, namespace, timeout):
        self.calls.append((argv, deepcopy(files)))
        if "--help" in argv: return {"exitCode": 0, "stdout": "Options:\n --dry-run='none'", "stderr": ""}
        if argv[0] == "get": return {"exitCode": 0, "stdout": '{"kind":"List","items":[]}', "stderr": "", "outputTruncated": False}
        failed = self.fail_preview if "--dry-run=server" in argv else self.fail_actual
        return {"exitCode": 1 if failed else 0, "stdout": "quota/planned-quota created", "stderr": "denied" if failed else "", "outputTruncated": False}


def broker(monkeypatch):
    monkeypatch.setattr(ApprovalBroker, "_start_waiter", lambda *_: None)
    cfg = SimpleNamespace(cluster_operator_mode="cluster", slack_bot_token="", slack_channel_id="", approval_timeout_seconds=300)
    instance = ApprovalBroker(None, AuditStore(), cfg)
    runner = Runner(); instance.executor = KubectlExecutor(KubeConfig(), runner=runner)
    return instance, runner


def test_general_kinds_and_cross_namespace_are_not_patch_catalog_limited():
    draft = canonical_plan(plan(), "kravel-demo")
    assert draft["id"].startswith("plan-")
    assert "resourcequota" in draft["command"] and "default" in draft["command"]
    for kind in ("CustomResourceDefinition", "ClusterRoleBinding", "PersistentVolume", "NetworkPolicy", "Secret", "CronJob"):
        value = plan(); value["files"]["quota.yaml"] = f"apiVersion: v1\nkind: {kind}\nmetadata:\n  name: proposed\n"
        assert canonical_plan(value)["eligible"]


@pytest.mark.parametrize("argv", [["bash", "-c", "kubectl get pods"], ["get", "pods", "--token=x"], ["get", "pods", "--raw=/api/v1/secrets"], ["get", "pods", "--kubeconfig=other"], ["apply", "-f", "https://example.com/private.yaml"], ["apply", "-f", "../secrets"], ["create", "configmap", "x", "--from-file=/proc/self/environ"], ["create", "secret", "generic", "x", "--from-env-file=/proc/self/environ"], ["exec", "pod/foo", "-it", "--", "sh"], ["logs", "foo", "-f"], ["create", "deployment", "foo", "--dry-run=none"], ["get", "pods", "--profile-output=out"], ["get", "pods", "-shttps://other"], ["delete", "pods", "--help"]])
def test_plans_cannot_change_identity_host_paths_or_shell(argv):
    with pytest.raises(ValueError): validate_argv(argv)


@pytest.mark.parametrize("argv", [["get", "pods", "--kuberc=/etc/private"], ["get", "secret", "-o", "go-template-file=/proc/self/environ"], ["get", "pods", "-ogo-template-file=/etc/private"], ["get", "pods", "--output=jsonpath-file=/etc/private"], ["apply", "-fhttps://example.com/file?x=y"], ["apply", "-k/tmp/unreviewed"]])
def test_general_plan_cannot_read_host_kuberc_or_output_template_files(argv):
    with pytest.raises(ValueError): validate_argv(argv)


@pytest.mark.parametrize("argv", [["get", "secret/approval", "-o", "jsonpath={.data.token}"], ["get", "secret/approval", "--template={{.data}}"], ["get", "pods", "--subresource=proxy"], ["exec", "pod/foo", "--", "ls"], ["delete", "pods", "--all"]])
def test_live_read_tool_cannot_mutate_or_escape_secret_redaction(argv):
    with pytest.raises(ValueError): validate_argv(argv, read_only=True)


def test_container_payload_is_reviewed_but_not_host_shell():
    assert validate_argv(["exec", "pod/foo", "--", "sh", "-c", "echo hello; ls /app"])
    assert validate_argv(["exec", "pod/foo", "--", "curl", "--user", "sample"])


def test_secret_output_and_last_applied_annotations_redacted_recursively():
    source = {"kind": "SecretList", "items": [{"metadata": {"annotations": {"kubectl.kubernetes.io/last-applied-configuration": "sensitive"}}, "data": {"token": "unrecognizable-value"}, "stringData": {"key": "sensitive"}}]}
    result = safe_output(json.dumps(source))
    assert json.loads(result)["items"][0]["data"]["token"] == "<redacted>"
    assert "sensitive" not in result and "unrecognizable-value" not in result and "<redacted>" in result
    assert "unrecognizable-value" not in safe_output('{"kind":"Secret","data":{"key":"unrecognizable-value"')
    yaml_result = safe_output("apiVersion: v1\nkind: Secret\nmetadata:\n  name: sample\ndata:\n  key: unrecognizable-value\n")
    assert json.loads(yaml_result)["data"]["key"] == "<redacted>"


def test_plan_files_are_exact_not_truncated_and_secrets_are_not_sent_to_slack():
    value = plan(); source = "# configuration\n" * 600
    value["files"]["quota.yaml"] = source
    draft = canonical_plan(value)
    store = AuditStore(); store.save_cluster_plan("run", draft)
    assert store.cluster_plan("run", draft["id"])["plan"]["files"]["quota.yaml"] == source
    value["files"]["quota.yaml"] = "apiVersion: v1\nkind: Secret\nmetadata:\n  name: x\ndata:\n  harmless: dmFsdWU=\n"
    with pytest.raises(ValueError, match="literal Secret"): canonical_plan(value)


def test_only_dry_runs_before_approval_and_execution_is_once(monkeypatch):
    instance, runner = broker(monkeypatch)
    pending = instance.create_plan(plan(), "kravel-demo")
    assert pending["status"] == "pending"
    assert all(a[0] == "get" or "--help" in a or "--dry-run=server" in a for a, _ in runner.calls)
    assert instance._execute(pending) is None
    approved = instance.approve(pending["id"], "human")
    instance._execute(approved)
    writes = [a for a, _ in runner.calls if a[0] == "apply" and "--help" not in a and "--dry-run=server" not in a]
    assert len(writes) == 1
    assert instance.store.proposal(pending["id"])["status"] == "executed"
    instance._execute(approved)
    assert len([a for a, _ in runner.calls if a[0] == "apply" and "--help" not in a and "--dry-run=server" not in a]) == 1


def test_exec_not_run_in_preview_and_requires_risk_acknowledgment(monkeypatch):
    instance, runner = broker(monkeypatch)
    value = plan(); value["steps"] = [{"label": "Container command", "argv": ["exec", "pod/foo", "--", "ls", "/app"]}]
    pending = instance.create_plan(value, "kravel-demo")
    assert runner.calls == [] and pending["dryRun"][0]["validation"] == "not_available"
    with pytest.raises(ValueError, match="acknowledge"): instance.approve(pending["id"], "human")
    approved = instance.approve(pending["id"], "human", accept_unvalidated=True)
    instance._execute(approved)
    assert len(runner.calls) == 1 and runner.calls[0][0][0] == "exec"


def test_tampered_files_never_execute(monkeypatch):
    instance, runner = broker(monkeypatch)
    pending = instance.create_plan(plan(), "kravel-demo")
    rows = deepcopy(pending["dryRun"]); rows[0]["reviewedPlan"]["plan"]["files"]["quota.yaml"] += "# altered\n"
    with instance.store.lock, instance.store.connection:
        instance.store.connection.execute("UPDATE proposals SET dry_run_json=? WHERE id=?", (json.dumps(rows), pending["id"]))
    instance._execute(instance.approve(pending["id"], "human"))
    assert instance.store.proposal(pending["id"])["status"] == "failed"
    assert all(a[0] == "get" or "--help" in a or "--dry-run=server" in a for a, _ in runner.calls)


def test_partial_failure_stops_and_does_not_roll_back_or_replay(monkeypatch):
    instance, runner = broker(monkeypatch)
    pending = instance.create_plan(plan(), "kravel-demo"); runner.fail_actual = True
    instance._execute(instance.approve(pending["id"], "human"))
    actual = [a for a, _ in runner.calls if a[0] != "get" and "--help" not in a and "--dry-run=server" not in a]
    assert len(actual) == 1 and actual[0][0] == "apply"
    assert instance.store.proposal(pending["id"])["status"] == "failed"


def test_failed_validation_needs_explicit_dependencies_not_silent_success(monkeypatch):
    instance, runner = broker(monkeypatch); runner.fail_preview = True
    with pytest.raises(ValueError, match="failed Kubernetes"): instance.create_plan(plan(), "kravel-demo")
    value = plan(); value["steps"].insert(0, {"label": "Namespace prerequisite", "argv": ["exec", "pod/foo", "--", "true"]}); value["steps"][1]["dependsOn"] = [1]
    pending = instance.create_plan(value, "kravel-demo")
    assert pending["dryRun"][1]["validation"] == "deferred"
    with pytest.raises(ValueError): instance.approve(pending["id"], "human")


def test_operator_enablement_and_expiry(monkeypatch):
    instance, runner = broker(monkeypatch); instance.config.cluster_operator_mode = "disabled"
    with pytest.raises(ValueError): instance.create_plan(plan(), "kravel-demo")
    instance.config.cluster_operator_mode = "cluster"; instance.config.approval_timeout_seconds = -1
    pending = instance.create_plan(plan(), "kravel-demo")
    assert instance.approve(pending["id"], "human")["status"] == "expired"
    assert all(a[0] == "get" or "--help" in a or "--dry-run=server" in a for a, _ in runner.calls)


def test_captured_target_changes_stop_all_writes(monkeypatch):
    instance, runner = broker(monkeypatch)
    pending = instance.create_plan(plan(), "kravel-demo")
    instance.executor.runner = lambda argv, files, namespace, timeout: {"exitCode": 0, "stdout": '{"kind":"List","items":[{"metadata":{"name":"unexpected","uid":"new"}}]}', "stderr": "", "outputTruncated": False}
    instance._execute(instance.approve(pending["id"], "human"))
    assert instance.store.proposal(pending["id"])["status"] == "failed"
    assert all(a[0] == "get" or "--help" in a or "--dry-run=server" in a for a, _ in runner.calls)


def test_cluster_admin_binding_only_grants_executor_not_worker():
    from pathlib import Path
    import yaml
    objects = list(yaml.safe_load_all(Path("deploy/local.yaml").read_text()))
    grant = next(o for o in objects if o.get("kind") == "ClusterRoleBinding" and o["metadata"]["name"] == "kravel-approved-cluster-operator")
    assert grant["roleRef"]["name"] == "cluster-admin"
    assert grant["subjects"] == [{"kind": "ServiceAccount", "name": "kravel-approval-broker", "namespace": "kravel-system"}]
    worker = next(o for o in objects if o.get("kind") == "ClusterRole" and o["metadata"]["name"] == "kravel-debugger-readonly")
    assert all(set(r["verbs"]) <= {"get", "list", "watch"} and "secrets" not in r["resources"] for r in worker["rules"])


def test_slack_posts_complete_files_before_root_becomes_ready():
    from kravel.slack import SlackApprovalClient
    draft = canonical_plan(plan()); draft["plan"]["files"]["code.txt"] = "complete-source\n" * 800
    client = SlackApprovalClient("not-a-real-credential", "demo-channel")
    calls = []
    def invoke(method, payload): calls.append((method, payload)); return {"channel": "demo-channel", "ts": str(len(calls))}
    client._call = invoke
    proposal = {"id": "test-proposal", "fix_id": draft["id"], "command": draft["command"], "dryRun": [{"validation": "not_available", "reviewedPlan": draft}], "expires_at": "future"}
    result = client.post(proposal)
    assert result["ts"] != "1" and calls[-2][0] == "chat.postMessage" and calls[-2][1]["text"].startswith("READY") and "do not approve" in calls[-1][1]["text"]
    chunks = [payload["text"].split("\n", 1)[1] for method, payload in calls if method == "chat.postMessage" and payload.get("thread_ts") and payload["text"].startswith("Generated file: code.txt")]
    assert "".join(chunks) == draft["plan"]["files"]["code.txt"]


def test_local_model_routing_without_cloud_or_parallel_agents():
    from kravel.model_routing import route_turn
    cfg = SimpleNamespace(llm_model="fast", llm_thinking_model="think", llm_routing="auto")
    assert select_model(cfg, "List pods")["model"] == "fast"
    assert select_model(cfg, "Create a deployment and service with RBAC")["model"] == "think"
    assert select_model(cfg, "Explain a complex CRD", learning=True)["model"] == "fast"
    assert select_model(cfg, "List pods", override="thinking")["model"] == "think"
    assert select_model(cfg, "Complex migration", override="fast")["model"] == "fast"
    routing = select_model(cfg, "Complex migration")
    assert [route_turn(cfg, routing, t)["model"] for t in range(1, 7)] == ["fast", "think", "fast", "fast", "fast", "fast"]
    assert route_turn(cfg, routing, 2, plan_ready=True)["model"] == "fast"
    assert route_turn(cfg, routing, 2, learning=True)["role"] == "teacher"


@pytest.mark.parametrize("question", ["Create a CronJob", "Scale the StatefulSet", "Drain the Kubernetes node", "Configure a NetworkPolicy", "Create a PVC"])
def test_general_operations_are_in_preflight_scope(question):
    assert guard_request_scope(question)["decision"] == "allow"
