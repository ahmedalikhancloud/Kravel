from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_cluster_arcade_contains_real_resource_controls_and_custom_cursors():
    html = (ROOT / "kravel" / "web" / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "kravel" / "web" / "app.css").read_text(encoding="utf-8")
    js = (ROOT / "kravel" / "web" / "app.js").read_text(encoding="utf-8")

    assert 'id="clusterWorld"' in html and 'id="resourceWorld"' in html
    scene = (ROOT / "kravel" / "web" / "scene.js").read_text(encoding="utf-8")
    assert "WebGLRenderer" in scene and "OrbitControls" in scene
    assert "CapsuleGeometry" in scene and "TubeGeometry" in scene and "Raycaster" in scene
    assert 'id="showConnections"' in html and 'id="resourceSearch"' in html
    assert 'role="tablist"' in html and "prefers-reduced-motion" in css
    assert "cursor-pointer.svg" in css
    assert 'runTool(tool)' in js and "AbortController" in js
    assert "innerHTML" not in js and "innerHTML" not in scene
    assert "./vendor/three.module.min.js" in scene and "https://" not in scene


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


def test_operator_identity_is_separate_and_not_in_guided_ui():
    manifest = (ROOT / "deploy" / "local.yaml").read_text(encoding="utf-8")
    operator_role = manifest.split("name: kravel-demo-human-operator", 1)[1].split("---", 1)[0]
    assert 'resourceNames: ["demo-gateway"]' in operator_role
    assert 'verbs: ["patch"]' in operator_role
    assert not any(verb in operator_role for verb in ("secrets", "pods/exec", '"create"', '"delete"'))
    debugger_pod = manifest.split("name: kravel\n  namespace", 1)[1].split("---", 1)[0]
    assert "KRAVEL_OPERATOR_TOKEN" not in debugger_pod and "KRAVEL_APPROVAL_TOKEN" not in debugger_pod
    tools = (ROOT / "kravel" / "tools.py").read_text(encoding="utf-8")
    assert "console" not in tools and "operator" not in tools
    api = (ROOT / "kravel" / "api.py").read_text(encoding="utf-8")
    assert "/v1/console/command" not in api
    html = (ROOT / "kravel" / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="labControls"' in html and 'id="auditTrail"' not in html
    assert 'src="http://127.0.0.1:8082/"' in html
    lab_js = (ROOT / "kravel" / "web" / "labs.js").read_text(encoding="utf-8")
    assert "innerHTML" not in lab_js and "localStorage" not in lab_js
    assert 'labs/preview' in lab_js and 'labs/confirm' in lab_js
    assert 'confirm' in lab_js and 'history.replaceState' in lab_js
    app_js = (ROOT / "kravel" / "web" / "app.js").read_text(encoding="utf-8")
    assert "kravel-lab-parent-ready" in lab_js and "kravel-lab-parent-ready" in app_js
    assert 'event.source === window.parent' in lab_js
    assert 'event.source !== elements.labControls.contentWindow' in app_js
    assert 'id="runSteps"' in html and 'id="demoGuide"' in html
    assert 'Human console' not in html and 'Audit trail' not in html


def test_observability_hub_has_guided_contextual_navigation_without_guessing_ids():
    from kravel.api import WEB_ASSETS
    html = (ROOT / "kravel/web/index.html").read_text(encoding="utf-8")
    js = (ROOT / "kravel/web/app.js").read_text(encoding="utf-8")
    links = (ROOT / "kravel/web/observability.mjs").read_text(encoding="utf-8")
    assert WEB_ASSETS["/ui/observability.mjs"][0] == "observability.mjs"
    for element_id in ("observability", "observeRequest", "requestShortcuts", "evaluationHistory", "evaluationLinks", "evaluationSkippedGroup", "refreshInsights"):
        assert f'id="{element_id}"' in html
    assert "60-second guide" in html and "Show execution timeline" in html
    assert "bash demo/local/demo.sh --connect-only" in html
    assert "noopener noreferrer" in js and "aria-disabled" in js
    assert 'payload.experimentId || "1"' not in js
    assert "selectedEvaluationId=" not in js
    assert "innerHTML" not in js and "innerHTML" not in links
    assert "pendingEvaluationRuns" in js and 'chooseEvaluation(evaluationJobs, selectedEvaluationId)' in js
    assert 'scorerExperimentId, row.traceId' in js
    assert 'state.currentRun?.id !== state.runId' in js
    assert 'state.currentRun.id !== state.runId' in js


def test_runbooks_and_new_repair_drafts_are_visible_without_permission_expansion():
    from kravel.api import WEB_ASSETS
    html = (ROOT / "kravel/web/index.html").read_text(encoding="utf-8")
    js = (ROOT / "kravel/web/runbooks.mjs").read_text(encoding="utf-8")
    manifest = (ROOT / "deploy/local.yaml").read_text(encoding="utf-8")
    for element_id in ("runbookLibrary", "runbookSearch", "runbookGroup", "runbookContext", "draftRepairs"):
        assert f'id="{element_id}"' in html
    assert WEB_ASSETS["/ui/runbooks.mjs"][0] == "runbooks.mjs"
    assert "innerHTML" not in js and "not observed causes" in js
    assert "Request server dry-run & human review" in js
    assert "mountPath: /repair-policy, readOnly: true" in manifest
    debugger_role = manifest.split("name: kravel-debugger-readonly", 1)[1].split("---", 1)[0]
    assert "patch" not in debugger_role and "secrets" not in debugger_role
