from __future__ import annotations

import copy
import json
import math
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse


def to_iso(value=None, field: str = "timestamp") -> str:
    if value in (None, ""):
        dt = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        dt = value
    else:
        text = str(value).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{field} is not a valid timestamp") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_duration_seconds(value, fallback: float = 900) -> float:
    if value in (None, ""):
        return fallback
    if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
        return float(value)
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([smhd])?", str(value).strip(), re.I)
    if not match:
        raise ValueError("duration must look like 30s, 15m, 2h, or 1d")
    factors = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return float(match.group(1)) * factors[(match.group(2) or "s").lower()]


def subtract_seconds(timestamp: str, seconds: float) -> str:
    dt = datetime.fromisoformat(to_iso(timestamp).replace("Z", "+00:00")) - timedelta(seconds=seconds)
    return to_iso(dt)


def is_internal_hostname(hostname: str) -> bool:
    value = (hostname or "").lower()
    return (
        value in {"localhost", "127.0.0.1", "::1", "host.docker.internal", "model-runner.docker.internal"}
        or value.endswith(".docker.internal")
        or value.endswith(".svc")
        or value.endswith(".svc.cluster.local")
        or ("." not in value and bool(re.fullmatch(r"[a-z0-9-]+", value)))
    )


def safe_service_url(value: str, label: str) -> str:
    parsed = urlparse(str(value or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{label} URL must use http or https")
    if parsed.scheme == "http" and not is_internal_hostname(parsed.hostname):
        raise ValueError(f"Remote {label} must use HTTPS")
    return value.rstrip("/")


def stable_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _escape_path(value: str) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


_MISSING = object()


def diff_json(before=_MISSING, after=_MISSING, path: str = "") -> list[dict]:
    if before is not _MISSING and after is not _MISSING and stable_json(before) == stable_json(after):
        return []
    if before is _MISSING:
        return [{"op": "add", "path": path, "value": after}]
    if after is _MISSING:
        return [{"op": "remove", "path": path}]
    if not isinstance(before, dict) or not isinstance(after, dict):
        return [{"op": "replace", "path": path, "value": after}]
    operations: list[dict] = []
    for key in sorted(set(before) | set(after)):
        operations.extend(diff_json(before.get(key, _MISSING), after.get(key, _MISSING), f"{path}/{_escape_path(key)}"))
    return operations


def changed_paths(operations: list[dict], limit: int = 12) -> list[str]:
    paths = [operation["path"] for operation in operations]
    return paths if len(paths) <= limit else [*paths[:limit], f"… +{len(paths) - limit} more"]


def sanitize_object(value: dict) -> dict:
    obj = copy.deepcopy(value)
    metadata = obj.get("metadata")
    if isinstance(metadata, dict):
        metadata.pop("managedFields", None)
        metadata.pop("selfLink", None)
    if obj.get("kind") == "Secret":
        for field in ("data", "stringData"):
            if isinstance(obj.get(field), dict):
                obj[field] = {key: "<redacted>" for key in obj[field]}
    return obj


def object_identity(obj: dict) -> dict:
    metadata = obj.get("metadata") or {}
    kind = obj.get("kind")
    name = metadata.get("name")
    if not kind or not name:
        raise ValueError("Kubernetes object requires kind and metadata.name")
    api_version = obj.get("apiVersion") or "v1"
    namespace = metadata.get("namespace") or ""
    return {
        "apiVersion": api_version,
        "kind": kind,
        "namespace": namespace,
        "name": name,
        "uid": metadata.get("uid") or "",
        "resourceVersion": metadata.get("resourceVersion") or "",
        "key": f"{api_version}|{kind}|{namespace or '_cluster'}|{name}",
    }
