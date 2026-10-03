import json
import pytest

from kravel import image_research as research
from kravel.cluster_plans import verify_replacement_images, MissingRepairInput
from kravel.investigation_tools import authorize_tool


def tag(name, architecture="amd64"):
    return {"name": name, "v2": True, "tag_status": "active", "digest": "sha256:" + "a" * 64, "images": [{"os": "linux", "architecture": architecture, "digest": "sha256:" + "b" * 64}], "last_updated": "2026-09-01T00:00:00Z"}


def test_candidates_are_real_pinned_filtered_and_do_not_claim_compatibility(monkeypatch):
    paths = []
    def get(path):
        paths.append(path)
        return 200, {"next": "ignored-private-pagination-url", "results": [tag("latest"), tag("v1.19.3-debian-1.0"), tag("v1.20.0-rc1"), tag("v1.18.0", "arm64")]}
    monkeypatch.setattr(research, "_get", get)
    result = research.search_image_tags("fluentd")
    assert len(paths) == 1 and paths[0] == "/v2/namespaces/library/repositories/fluentd/tags?page_size=100"
    assert [c["tag"] for c in result["candidates"]] == ["v1.19.3-debian-1.0"]
    assert result["candidates"][0]["digestReference"].startswith("docker.io/library/fluentd@sha256:")
    assert "not application compatibility" in result["notice"] and result["truncated"]
    assert "ignored-private" not in json.dumps(result)


@pytest.mark.parametrize("args", [{"repository":"private/company"}, {"repository":"https://localhost/"}, {"repository":"fluentd", "prefix":"secret-customer-id"}, {"repository":"fluentd", "tag":"../private"}, {"repository":"fluentd", "tag":"version?token=secret"}])
def test_arbitrary_repositories_payloads_urls_and_tag_paths_are_rejected(args):
    with pytest.raises(ValueError): authorize_tool("inspect_image_tag" if "tag" in args else "search_image_tags", args, "kravel-demo")


@pytest.mark.parametrize("status,expected", [(404,"not_found"),(429,"unavailable"),(403,"unavailable"),(302,"unavailable")])
def test_missing_rate_limited_and_redirect_results_are_never_verified(monkeypatch,status,expected):
    monkeypatch.setattr(research,"_get",lambda _: (status, {}))
    assert research.inspect_image_tag("fluentd","1.10")["status"] == expected
    assert research.search_image_tags("fluentd")["candidates"] == []


def test_private_registry_dns_is_rejected_before_any_connection(monkeypatch):
    monkeypatch.setattr(research.socket,"getaddrinfo", lambda *_args,**_kwargs: [(0,0,0,"",("127.0.0.1",443))])
    with pytest.raises(ValueError,match="public IPs"): research.inspect_image_tag("fluentd","1.10")


def test_registry_evidence_supports_new_tag_but_missing_tag_or_runbook_does_not(monkeypatch):
    monkeypatch.setattr(research, "_get", lambda _: (200, {"results": [tag("v1.19.3-debian-1.0")]}))
    result = research.search_image_tags("fluentd")
    old = {"kind":"DaemonSet","metadata":{"name":"example-daemonset"},"spec":{"template":{"spec":{"containers":[{"name":"fluentd","image":"fluentd:1.10"}]}}}}
    plan = {"files":{},"steps":[{"argv":["set","image","daemonset/example-daemonset","fluentd=fluentd:v1.19.3-debian-1.0"]}]}
    evidence = [{"status":"observed","body":{"items":[old]}},{"status":"observed","sourceType":"reference","label":"Published image candidates","body":result}]
    verify_replacement_images(plan,evidence,"Fix example-daemonset","kravel-demo")
    evidence[-1]["body"]["status"] = "not_found"
    with pytest.raises(MissingRepairInput): verify_replacement_images(plan,evidence,"Fix example-daemonset","kravel-demo")
    evidence[-1]["body"]["status"] = "verified"; evidence[-1]["label"] = "Focused read · search_runbooks"
    with pytest.raises(MissingRepairInput): verify_replacement_images(plan,evidence,"Fix example-daemonset","kravel-demo")


def test_list_items_without_kind_still_require_verified_replacement():
    old = {"metadata":{"name":"example-daemonset"},"spec":{"template":{"spec":{"containers":[{"name":"fluentd","image":"fluentd:broken"}]}}}}
    evidence = [{"label":"Per-node workload ownership & rollout","status":"observed","body":{"items":[old]}}]
    plan = {"files":{},"steps":[{"argv":["set","image","ds/example-daemonset","fluentd=fluentd:invented"]}]}
    with pytest.raises(MissingRepairInput): verify_replacement_images(plan,evidence,"Fix the daemonset","kravel-demo")
