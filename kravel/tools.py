from __future__ import annotations

from .utils import parse_duration_seconds


TEMPORAL_TOOLS = [
    {"type": "function", "function": {"name": "rewind_cluster_state", "description": "Reconstruct the exact last-observed Kubernetes object state at a timestamp.", "parameters": {"type": "object", "properties": {"timestamp": {"type": "string"}, "kinds": {"type": "array", "items": {"type": "string"}}, "resource_key": {"type": "string"}}, "required": ["timestamp"]}}},
    {"type": "function", "function": {"name": "diff_states", "description": "Return deterministic Kubernetes object changes between two timestamps.", "parameters": {"type": "object", "properties": {"from": {"type": "string"}, "to": {"type": "string"}, "kinds": {"type": "array", "items": {"type": "string"}}}, "required": ["from", "to"]}}},
    {"type": "function", "function": {"name": "get_incident_context", "description": "Build a time-bounded evidence shard from state changes, Kubernetes events, and metrics. Relevance is not proof of causality.", "parameters": {"type": "object", "properties": {"incident_at": {"type": "string"}, "lookback": {"type": ["string", "number"]}, "resource_key": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, "required": ["incident_at"]}}},
    {"type": "function", "function": {"name": "trace_resource", "description": "Trace ownership, selectors, ConfigMap, Secret, volume, and service-account relationships at a timestamp.", "parameters": {"type": "object", "properties": {"timestamp": {"type": "string"}, "resource_key": {"type": "string"}, "max_depth": {"type": "integer", "minimum": 0, "maximum": 8}}, "required": ["timestamp", "resource_key"]}}},
]


def enforce_scope(name: str, args: dict, config, baseline_at: str, incident_at: str, namespace: str) -> dict:
    scoped = dict(args or {})
    scoped["cluster_id"] = config.cluster_id
    if name != "trace_resource":
        if namespace:
            scoped["namespace"] = namespace
        else:
            scoped.pop("namespace", None)
    resource_key = scoped.get("resource_key")
    if namespace and resource_key and str(resource_key).split("|")[2] != namespace:
        raise ValueError(f"resource_key is outside the fixed namespace scope {namespace}")
    if name == "rewind_cluster_state":
        scoped.setdefault("timestamp", incident_at)
    elif name == "diff_states":
        scoped.setdefault("from", baseline_at)
        scoped.setdefault("to", incident_at)
    elif name == "get_incident_context":
        scoped.setdefault("incident_at", incident_at)
        scoped["limit"] = min(max(int(scoped.get("limit", 20)), 1), 30)
    elif name == "trace_resource":
        scoped.setdefault("timestamp", incident_at)
    return scoped


def execute_temporal_tool(name: str, args: dict, store, config, embedding_client=None):
    cluster_id = args.get("cluster_id", config.cluster_id)
    if name == "rewind_cluster_state":
        return store.state_at(cluster_id=cluster_id, timestamp=args.get("timestamp"), namespace=args.get("namespace"), kinds=args.get("kinds"), resource_key=args.get("resource_key"))
    if name == "diff_states":
        return store.diff_states(cluster_id=cluster_id, from_at=args.get("from"), to_at=args.get("to"), namespace=args.get("namespace"), kinds=args.get("kinds"))
    if name == "get_incident_context":
        return store.context_shard(cluster_id=cluster_id, incident_at=args.get("incident_at"), lookback=parse_duration_seconds(args.get("lookback"), 900), namespace=args.get("namespace", ""), resource_key=args.get("resource_key", ""), limit=args.get("limit", 20))
    if name == "trace_resource":
        return store.trace_resource(cluster_id=cluster_id, timestamp=args.get("timestamp"), resource_key=args.get("resource_key"), max_depth=args.get("max_depth", 2))
    raise ValueError(f"Unknown tool: {name}")
