import pytest

from kravel.tools import _container_issue, discover_issues, enforce_read_scope


class FakeKube:
    def list_resources(self, kind, namespace, **_kwargs):
        if kind == "pods":
            return {"items": [
                self.pod("oom-demo-a", "oom-demo", last_reason="OOMKilled"),
                self.pod("image-demo-a", "image-demo", waiting="ImagePullBackOff"),
                self.pod("crash-demo-a", "crash-demo", waiting="CrashLoopBackOff"),
                self.pod("config-demo-a", "config-demo", waiting="CrashLoopBackOff"),
            ]}
        if kind == "deployments":
            return {"items": [self.deployment(name) for name in ("oom-demo", "image-demo", "crash-demo", "config-demo")]}
        if kind == "replicasets":
            return {"items": []}
        if kind == "configmaps":
            return {"items": [{"metadata": {"name": "config-demo"}, "data": {"MODE": "broken"}}, {"metadata": {"name": "kube-root-ca.crt"}, "data": {"ca.crt": "redacted"}}]}
        if kind == "services":
            return {"items": [{"metadata": {"name": "demo-service"}, "spec": {"type": "ClusterIP"}}]}
        raise AssertionError(kind)

    def get_resource(self, kind, name, namespace):
        assert (kind, name, namespace) == ("configmaps", "config-demo", "kravel-demo")
        return {"object": {"data": {"MODE": "broken"}}}

    @staticmethod
    def pod(name, app, waiting="", last_reason=""):
        state = {"waiting": {"reason": waiting}} if waiting else {"running": {}}
        return {"metadata": {"name": name, "labels": {"app": app}}, "status": {"phase": "Running", "containerStatuses": [{"name": app, "ready": not waiting, "state": state, "lastState": {"terminated": {"reason": last_reason}}}]}}

    @staticmethod
    def deployment(name):
        return {"metadata": {"name": name, "labels": {"app": name}}, "spec": {"replicas": 1}, "status": {"availableReplicas": 0}}


def test_discovers_four_independent_demo_failures_and_visual_resources():
    result = discover_issues(FakeKube(), "kravel-demo")

    assert {item["type"] for item in result["issues"]} == {"oomkilled", "imagepullbackoff", "crashloopbackoff", "bad_configmap"}
    assert {item["kind"] for item in result["resources"]} == {"Pod", "Deployment", "ConfigMap", "Service"}
    assert not any(item["name"] == "kube-root-ca.crt" for item in result["resources"])
    assert next(item for item in result["resources"] if item["name"] == "oom-demo" and item["kind"] == "Deployment")["health"] == "critical"


def test_read_scope_blocks_secrets_and_cross_namespace_wildcards():
    with pytest.raises(ValueError):
        enforce_read_scope("get_resource", {"kind": "secrets", "name": "x", "namespace": "kravel-demo"}, "kravel-demo")
    with pytest.raises(ValueError):
        enforce_read_scope("get_pods", {"namespace": "*"}, "kravel-demo")


def test_read_scope_bounds_log_volume():
    result = enforce_read_scope("pod_logs", {"pod": "x", "namespace": "kravel-demo", "tail_lines": 99999}, "kravel-demo")
    assert result["tail_lines"] == 500


def test_generic_failure_is_diagnosed_without_offering_a_demo_fix():
    pod = FakeKube.pod("production-api-a", "production-api", waiting="ImagePullBackOff")
    issue_type, fix_id, _evidence = _container_issue(pod)
    assert issue_type == "imagepullbackoff"
    assert fix_id == ""


def test_fix_suggestions_require_matching_failure_and_demo_namespace():
    pod = FakeKube.pod("oom-demo-a", "oom-demo", waiting="ImagePullBackOff")
    assert _container_issue(pod)[1] == ""
    pod = FakeKube.pod("image-demo-a", "image-demo", waiting="ImagePullBackOff")
    assert _container_issue(pod, "production")[1] == ""


def test_model_cannot_switch_selected_namespace():
    with pytest.raises(ValueError):
        enforce_read_scope("get_pods", {"namespace": "production"}, "kravel-demo")


def test_deleted_pods_do_not_leave_ghost_failures():
    class DeletingKube(FakeKube):
        def list_resources(self, kind, namespace, **kwargs):
            result = super().list_resources(kind, namespace, **kwargs)
            if kind == "pods":
                for pod in result["items"]:
                    pod["metadata"]["deletionTimestamp"] = "2026-09-30T12:00:00Z"
            return result
    result = discover_issues(DeletingKube(), "kravel-demo")
    assert result["podCount"] == 0
    assert all(item["type"] == "bad_configmap" for item in result["issues"])


def test_snapshot_includes_active_replicasets_and_real_owner_edges_not_old_empty_revisions():
    class OwnedKube(FakeKube):
        def list_resources(self, kind, namespace, **kwargs):
            if kind == "replicasets":
                return {"items": [
                    {"metadata": {"name": "oom-revision", "uid": "rs1", "ownerReferences": [{"kind": "Deployment", "name": "oom-demo", "controller": True}]}, "spec": {"replicas": 1}},
                    {"metadata": {"name": "old-empty"}, "spec": {"replicas": 0}},
                ]}
            result = super().list_resources(kind, namespace, **kwargs)
            if kind == "pods":
                result["items"][0]["metadata"]["ownerReferences"] = [{"kind": "ReplicaSet", "name": "oom-revision", "uid": "rs1", "controller": True}]
            return result
    snapshot = discover_issues(OwnedKube(), "kravel-demo")
    assert [item["name"] for item in snapshot["resources"] if item["kind"] == "ReplicaSet"] == ["oom-revision"]
    assert len(snapshot["connections"]) == 2
    assert any(edge["source"] == "kravel-demo/ReplicaSet/oom-revision" and edge["target"] == "kravel-demo/Pod/oom-demo-a" for edge in snapshot["connections"])


def test_running_pod_without_container_status_is_not_counted_as_healthy():
    class UnknownKube(FakeKube):
        def list_resources(self, kind, namespace, **kwargs):
            result = super().list_resources(kind, namespace, **kwargs)
            if kind == "pods":
                for pod in result["items"]:
                    pod["status"]["containerStatuses"] = []
            return result
    snapshot = discover_issues(UnknownKube(), "kravel-demo")
    assert snapshot["healthyPods"] == 0
    assert not any(item["ready"] for item in snapshot["resources"] if item["kind"] == "Pod")
