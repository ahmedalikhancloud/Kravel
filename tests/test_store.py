from datetime import datetime, timedelta, timezone

from kravel.store import AuditStore


def test_audit_and_investigation_metrics_are_persisted():
    store = AuditStore()
    store.record("debugger", "tool.get_pods", actor="qwen", resource="kravel-demo", duration_ms=4.2)
    store.record_investigation(
        id="run-1", started_at="2026-09-30T12:00:00Z", finished_at="2026-09-30T12:00:01Z",
        namespace="kravel-demo", status="success", total_ms=1000, model_ms=800, tool_ms=100,
        tool_calls=2, input_guardrail_ms=2, output_guardrail_ms=3,
        mlflow_setup_ms=10, mlflow_overhead_ms=12, mlflow_flush_ms=15, trace_id="trace-1",
    )

    assert store.audit_entries()[0]["action"] == "tool.get_pods"
    run = store.investigations()[0]
    assert run["tool_calls"] == 2
    assert run["mlflow_flush_ms"] == 15
    assert store.stats()["investigations"] == 1


def test_proposal_state_and_structured_outputs_round_trip():
    store = AuditStore()
    expires = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    proposal = store.create_proposal(
        fix_id="fix_image_pull", namespace="kravel-demo", resource="Deployment/image-demo",
        command="kubectl ...", dry_run=[{"dryRun": True}], expires_at=expires,
    )
    updated = store.update_proposal(proposal["id"], status="executed", result={"operations": [{"ok": True}]})

    assert updated["dryRun"] == [{"dryRun": True}]
    assert updated["result"]["operations"][0]["ok"] is True
    assert store.stats()["executedFixes"] == 1
