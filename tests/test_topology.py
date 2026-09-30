from kravel.topology import build_connections, config_references, resource_key


def obj(kind, name, **extra):
    return {"kind": kind, "metadata": {"name": name, "uid": name, **extra.pop("metadata", {})}, **extra}


def links(objects, hidden=()):
    visible = {resource_key(item["kind"], item["metadata"]["name"], "demo") for item in objects} - set(hidden)
    return build_connections(objects, "demo", visible)


def test_owner_chain_requires_real_controller_reference_and_correct_uid():
    deployment = obj("Deployment", "api")
    rs = obj("ReplicaSet", "api-revision", metadata={"ownerReferences": [{"kind": "Deployment", "name": "api", "uid": "api", "controller": True}]})
    pod = obj("Pod", "api-pod", metadata={"ownerReferences": [{"kind": "ReplicaSet", "name": "api-revision", "uid": "api-revision", "controller": True}]})
    impostor = obj("Pod", "different", metadata={"ownerReferences": [{"kind": "ReplicaSet", "name": "api-revision", "uid": "stale-uid", "controller": True}]})
    unrelated = obj("Pod", "api-unowned", metadata={"labels": {"app": "api"}})
    result = links([pod, deployment, rs, impostor, unrelated])
    assert len(result) == 2
    assert {edge["relation"] for edge in result} == {"owns"}
    assert {edge["target"] for edge in result} == {"demo/ReplicaSet/api-revision", "demo/Pod/api-pod"}
    rs["metadata"]["ownerReferences"][0]["controller"] = False
    assert len(links([pod, deployment, rs])) == 1


def test_service_selector_is_and_match_and_empty_selectors_have_no_links():
    service = obj("Service", "gateway", spec={"selector": {"app": "api", "tier": "backend"}})
    exact = obj("Pod", "exact", metadata={"labels": {"app": "api", "tier": "backend", "version": "v1"}})
    partial = obj("Pod", "partial", metadata={"labels": {"app": "api"}})
    empty = obj("Service", "external", spec={})
    result = links([service, exact, partial, empty])
    assert len(result) == 1 and result[0]["target"] == "demo/Pod/exact"
    assert result[0]["relation"] == "selects" and "not a traffic measurement" in result[0]["evidence"]


def test_config_references_cover_volumes_projected_env_and_init_containers_not_secrets():
    spec = {"volumes": [{"configMap": {"name": "volume"}}, {"projected": {"sources": [{"configMap": {"name": "projected"}}, {"secret": {"name": "private"}}]}}],
            "containers": [{"env": [{"valueFrom": {"configMapKeyRef": {"name": "env"}}}], "envFrom": [{"configMapRef": {"name": "bulk"}}, {"secretRef": {"name": "private"}}]}],
            "initContainers": [{"envFrom": [{"configMapRef": {"name": "init"}}]}]}
    assert config_references(spec) == {"volume", "projected", "env", "bulk", "init"}
    pod = obj("Pod", "app", spec=spec)
    dep = obj("Deployment", "app", spec={"template": {"spec": spec}})
    maps = [obj("ConfigMap", name) for name in config_references(spec)]
    result = links([pod, dep, *maps])
    assert len(result) == 10 and all(edge["relation"] == "configures" for edge in result)
    assert all("private" not in str(edge) for edge in result)


def test_hidden_or_missing_endpoints_never_create_dangling_edges():
    pod = obj("Pod", "app", spec={"volumes": [{"configMap": {"name": "config"}}, {"configMap": {"name": "missing"}}]})
    config = obj("ConfigMap", "config")
    assert len(links([pod, config])) == 1
    assert links([pod, config], hidden=["demo/ConfigMap/config"]) == []


def test_connections_are_deduplicated_and_order_deterministic():
    pod = obj("Pod", "app", spec={"volumes": [{"configMap": {"name": "config"}}, {"configMap": {"name": "config"}}]})
    config = obj("ConfigMap", "config")
    assert links([pod, config]) == links([config, pod])
    assert len(links([pod, config])) == 1
