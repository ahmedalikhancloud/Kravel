from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_WATCH_RESOURCES = [
    "/api/v1/pods",
    "/api/v1/configmaps",
    "/api/v1/services",
    "/api/v1/endpoints",
    "/api/v1/serviceaccounts",
    "/api/v1/persistentvolumeclaims",
    "/api/v1/namespaces",
    "/apis/apps/v1/deployments",
    "/apis/apps/v1/statefulsets",
    "/apis/apps/v1/daemonsets",
    "/apis/apps/v1/replicasets",
    "/apis/batch/v1/jobs",
    "/apis/batch/v1/cronjobs",
    "/apis/networking.k8s.io/v1/ingresses",
    "/apis/discovery.k8s.io/v1/endpointslices",
    "/apis/events.k8s.io/v1/events",
]


def _integer(name: str, fallback: int) -> int:
    value = os.getenv(name)
    if value in (None, ""):
        return fallback
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _number(name: str, fallback: float) -> float:
    value = os.getenv(name)
    if value in (None, ""):
        return fallback
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def _json(name: str, fallback):
    value = os.getenv(name)
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} must contain valid JSON") from exc


@dataclass(slots=True)
class PolicyConfig:
    high_confidence: float = 0.85
    minimum_margin: float = 0.2
    positive_threshold: float = 0.65


@dataclass(slots=True)
class KubeConfig:
    host: str = ""
    port: int = 443
    token_path: str = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    ca_path: str = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"


@dataclass(slots=True)
class Config:
    host: str
    port: int
    db_path: str
    cluster_id: str
    retention_days: int
    max_body_bytes: int
    api_token: str
    watch_resources: list[str]
    prometheus_url: str
    prometheus_interval_ms: int
    prometheus_queries: dict[str, str]
    embedding_url: str
    embedding_model: str
    embedding_api_key: str
    llm_base_url: str
    llm_model: str
    llm_api_key: str
    llm_max_turns: int
    llm_reasoning_budget: int
    llm_timeout_seconds: int
    laya_url: str
    laya_api_key: str
    laya_model: str
    mlflow_url: str
    mlflow_experiment: str
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    kube: KubeConfig = field(default_factory=KubeConfig)


def load_config() -> Config:
    return Config(
        host=os.getenv("KRAVEL_HOST", "127.0.0.1"),
        port=_integer("KRAVEL_PORT", 8080),
        db_path=os.getenv("KRAVEL_DB_PATH", str(Path.cwd() / "data" / "kravel.db")),
        cluster_id=os.getenv("KRAVEL_CLUSTER_ID", socket.gethostname()),
        retention_days=_integer("KRAVEL_RETENTION_DAYS", 30),
        max_body_bytes=_integer("KRAVEL_MAX_BODY_BYTES", 8 * 1024 * 1024),
        api_token=os.getenv("KRAVEL_API_TOKEN", ""),
        watch_resources=_json("KRAVEL_WATCH_RESOURCES", DEFAULT_WATCH_RESOURCES),
        prometheus_url=os.getenv("KRAVEL_PROMETHEUS_URL", ""),
        prometheus_interval_ms=_integer("KRAVEL_PROMETHEUS_INTERVAL_MS", 30_000),
        prometheus_queries=_json("KRAVEL_PROMETHEUS_QUERIES", {}),
        embedding_url=os.getenv("KRAVEL_EMBEDDING_URL", ""),
        embedding_model=os.getenv("KRAVEL_EMBEDDING_MODEL", ""),
        embedding_api_key=os.getenv("KRAVEL_EMBEDDING_API_KEY", ""),
        llm_base_url=os.getenv("KRAVEL_LLM_BASE_URL", "http://model-runner.docker.internal/engines/v1"),
        llm_model=os.getenv("KRAVEL_LLM_MODEL", "ai/qwen3:4b-instruct-2507-q4_K_M"),
        llm_api_key=os.getenv("KRAVEL_LLM_API_KEY", ""),
        llm_max_turns=_integer("KRAVEL_LLM_MAX_TURNS", 3),
        llm_reasoning_budget=_integer("KRAVEL_LLM_REASONING_BUDGET", 0),
        llm_timeout_seconds=_integer("KRAVEL_LLM_TIMEOUT_SECONDS", 90),
        laya_url=os.getenv("KRAVEL_LAYA_URL", "http://kravel-laya.kravel-ai.svc.cluster.local:8000"),
        laya_api_key=os.getenv("KRAVEL_LAYA_API_KEY", ""),
        laya_model=os.getenv("KRAVEL_LAYA_MODEL", "english"),
        mlflow_url=os.getenv("KRAVEL_MLFLOW_URL", "http://kravel-mlflow.kravel-observability.svc.cluster.local:5000"),
        mlflow_experiment=os.getenv("KRAVEL_MLFLOW_EXPERIMENT", "Kravel Local Incident Traces"),
        policy=PolicyConfig(
            high_confidence=_number("KRAVEL_POLICY_HIGH_CONFIDENCE", 0.85),
            minimum_margin=_number("KRAVEL_POLICY_MINIMUM_MARGIN", 0.2),
            positive_threshold=_number("KRAVEL_POLICY_POSITIVE_THRESHOLD", 0.65),
        ),
        kube=KubeConfig(
            host=os.getenv("KUBERNETES_SERVICE_HOST", ""),
            port=_integer("KUBERNETES_SERVICE_PORT_HTTPS", 443),
            token_path=os.getenv("KRAVEL_KUBE_TOKEN_PATH", "/var/run/secrets/kubernetes.io/serviceaccount/token"),
            ca_path=os.getenv("KRAVEL_KUBE_CA_PATH", "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"),
        ),
    )
