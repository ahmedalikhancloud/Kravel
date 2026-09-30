from __future__ import annotations

from datetime import datetime


IGNORED_PATHS = {"/metadata/resourceVersion", "/metadata/generation"}


def _meaningful(paths):
    return [path for path in paths if path not in IGNORED_PATHS and not path.startswith("/status")]


def _t_minus(event_at, incident_at):
    left = datetime.fromisoformat(event_at.replace("Z", "+00:00"))
    right = datetime.fromisoformat(incident_at.replace("Z", "+00:00"))
    seconds = max(0, round((right - left).total_seconds()))
    return f"T-{seconds}s" if seconds < 60 else f"T-{seconds // 60}m{seconds % 60:02d}s"


def render_report(store, cluster_id, baseline_at, incident_at, namespace):
    before = store.state_at(cluster_id=cluster_id, timestamp=baseline_at, namespace=namespace)
    after = store.state_at(cluster_id=cluster_id, timestamp=incident_at, namespace=namespace)
    diff = store.diff_states(cluster_id=cluster_id, from_at=baseline_at, to_at=incident_at, namespace=namespace)
    window = max(60, (datetime.fromisoformat(incident_at.replace("Z", "+00:00")) - datetime.fromisoformat(baseline_at.replace("Z", "+00:00"))).total_seconds() + 5)
    context = store.context_shard(cluster_id=cluster_id, incident_at=incident_at, lookback=window, namespace=namespace, limit=500)
    lines = ["", "KRAVEL TIME-TRAVEL INCIDENT REPORT", "==================================", f"Cluster:   {cluster_id}", f"Namespace: {namespace}", f"Baseline:  {baseline_at}", f"Incident:  {incident_at}", "", "Deterministic state diff"]
    for change in diff["changes"]:
        paths = _meaningful(change["changedPaths"])
        if paths or change["changeType"] != "modified":
            lines.append(f"  {change['changeType'].upper():8} {change['resourceKey']}")
            if paths:
                lines.append(f"           {', '.join(paths)}")
    lines.extend(["", "Ordered evidence timeline"])
    evidence = []
    for change in context["changes"]:
        paths = _meaningful([item.get("path", "") for item in change["patch"]])
        if paths:
            evidence.append((change["eventAt"], f"{change['action']:8} {change['kind']} {change['namespace']}/{change['name']} — {', '.join(paths)}"))
    for event in context["kubernetesEvents"]:
        if event["type"] == "Warning" or event["reason"] in {"BackOff", "Failed", "Unhealthy", "FailedScheduling"}:
            evidence.append((event["eventAt"], f"EVENT    {event['regardingKind']} {event['regardingName']}: {event['reason']} — {event['note']}"))
    for timestamp, text in sorted(evidence):
        lines.append(f"  {_t_minus(timestamp, incident_at):8} {timestamp}  {text}")
    lines.extend(["", "Reconstruction proof", f"  {before['objectCount']} objects at baseline; {after['objectCount']} at incident time; {diff['changeCount']} object diffs.", "  This is temporal correlation, not automatic proof of causality."])
    return "\n".join(lines)
