from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_cluster_arcade_contains_real_resource_controls_and_custom_cursors():
    html = (ROOT / "kravel" / "web" / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "kravel" / "web" / "app.css").read_text(encoding="utf-8")
    js = (ROOT / "kravel" / "web" / "app.js").read_text(encoding="utf-8")

    assert 'id="clusterWorld"' in html and 'id="resourceWorld"' in html
    assert ".iso-cube" in css and ".config-slab" in css and ".pod-shell" in css and ".service-ring" in css
    assert "cursor-pointer.svg" in css
    assert 'runTool("get_resource", true)' in js
    assert "innerHTML" not in js


def test_agent_and_broker_have_separate_least_privilege_roles():
    manifest = (ROOT / "deploy" / "local.yaml").read_text(encoding="utf-8")
    debugger_role = manifest.split("name: kravel-debugger-readonly", 1)[1].split("---", 1)[0]
    broker_role = manifest.split("name: kravel-demo-fix-executor", 1)[1].split("---", 1)[0]

    assert 'resources: ["pods/log"]' in debugger_role
    assert 'verbs: ["get", "list", "watch"]' in debugger_role
    assert "secrets" not in debugger_role and "patch" not in debugger_role and "delete" not in debugger_role
    assert 'resourceNames: ["oom-demo", "image-demo", "crash-demo", "config-demo"]' in broker_role
    assert 'verbs: ["get", "patch"]' in broker_role


def test_obsolete_time_travel_and_laya_assets_are_not_referenced_by_runtime():
    runtime = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "kravel").glob("*.py"))
    assert "rewind_cluster_state" not in runtime
    assert "TemporalStore" not in runtime
    assert "kravel.laya" not in runtime
