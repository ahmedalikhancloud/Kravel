from __future__ import annotations

import json
import sys

from .tools import execute_temporal_tool


PROTOCOL = "2025-11-25"
TOOLS = [
    {
        "name": "rewind_cluster_state",
        "description": "Reconstruct the exact last-observed Kubernetes object state at a timestamp.",
        "inputSchema": {"type": "object", "required": ["timestamp"], "properties": {"timestamp": {"type": "string"}, "cluster_id": {"type": "string"}, "namespace": {"type": "string"}, "kinds": {"type": "array", "items": {"type": "string"}}, "resource_key": {"type": "string"}}},
    },
    {
        "name": "diff_states",
        "description": "Return deterministic Kubernetes object changes between two timestamps.",
        "inputSchema": {"type": "object", "required": ["from", "to"], "properties": {"from": {"type": "string"}, "to": {"type": "string"}, "cluster_id": {"type": "string"}, "namespace": {"type": "string"}, "kinds": {"type": "array", "items": {"type": "string"}}}},
    },
    {
        "name": "get_incident_context",
        "description": "Build a bounded evidence shard. Relevance is not proof of causality.",
        "inputSchema": {"type": "object", "required": ["incident_at"], "properties": {"incident_at": {"type": "string"}, "lookback": {"type": ["string", "number"]}, "namespace": {"type": "string"}, "resource_key": {"type": "string"}, "limit": {"type": "integer"}}},
    },
    {
        "name": "trace_resource",
        "description": "Trace Kubernetes resource relationships at a timestamp.",
        "inputSchema": {"type": "object", "required": ["timestamp", "resource_key"], "properties": {"timestamp": {"type": "string"}, "resource_key": {"type": "string"}, "max_depth": {"type": "integer"}}},
    },
]


def run_mcp(store, config, input_stream=None, output_stream=None):
    input_stream, output_stream = input_stream or sys.stdin, output_stream or sys.stdout
    for line in input_stream:
        if not line.strip():
            continue
        message = None
        try:
            message = json.loads(line)
            method = message.get("method")
            if method == "initialize":
                result = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}}, "serverInfo": {"name": "kravel", "version": "0.2.0"}}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                value = execute_temporal_tool(message.get("params", {}).get("name"), message.get("params", {}).get("arguments", {}), store, config)
                result = {"content": [{"type": "text", "text": json.dumps(value, indent=2)}]}
            elif method == "ping":
                result = {}
            elif method and method.startswith("notifications/"):
                continue
            else:
                raise KeyError("method_not_found")
            reply = {"jsonrpc": "2.0", "id": message.get("id"), "result": result}
        except KeyError:
            reply = {"jsonrpc": "2.0", "id": (message or {}).get("id"), "error": {"code": -32601, "message": "Method not found"}}
        except Exception as exc:
            reply = {"jsonrpc": "2.0", "id": (message or {}).get("id"), "error": {"code": -32603, "message": str(exc)}}
        output_stream.write(json.dumps(reply) + "\n")
        output_stream.flush()
