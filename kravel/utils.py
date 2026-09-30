from __future__ import annotations

import copy
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlparse


def to_iso(value=None, field: str = "timestamp") -> str:
    if value in (None, ""):
        dt = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field} is not a valid timestamp") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def is_internal_hostname(hostname: str) -> bool:
    value = (hostname or "").lower()
    return value in {"localhost", "127.0.0.1", "::1", "host.docker.internal", "model-runner.docker.internal"} or value.endswith((".docker.internal", ".svc", ".svc.cluster.local")) or ("." not in value and bool(re.fullmatch(r"[a-z0-9-]+", value)))


def safe_service_url(value: str, label: str) -> str:
    parsed = urlparse(str(value or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{label} URL must use http or https")
    if parsed.scheme == "http" and not is_internal_hostname(parsed.hostname):
        raise ValueError(f"Remote {label} must use HTTPS")
    return value.rstrip("/")


def stable_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


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
