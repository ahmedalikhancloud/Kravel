from __future__ import annotations

import os
import socket
from dataclasses import dataclass, field
from pathlib import Path


def _integer(name: str, fallback: int) -> int:
    value = os.getenv(name)
    if value in (None, ""):
        return fallback
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


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
    max_body_bytes: int
    api_token: str
    default_namespace: str
    llm_base_url: str
    llm_model: str
    llm_api_key: str
    llm_max_turns: int
    llm_reasoning_budget: int
    llm_timeout_seconds: int
    mlflow_url: str
    mlflow_experiment: str
    broker_url: str
    approval_timeout_seconds: int
    approval_token: str
    slack_bot_token: str
    slack_channel_id: str
    operator_token: str = ""
    operator_url: str = ""
    kube: KubeConfig = field(default_factory=KubeConfig)


def load_config() -> Config:
    return Config(
        host=os.getenv("KRAVEL_HOST", "127.0.0.1"),
        port=_integer("KRAVEL_PORT", 8080),
        db_path=os.getenv("KRAVEL_DB_PATH", str(Path.cwd() / "data" / "kravel.db")),
        cluster_id=os.getenv("KRAVEL_CLUSTER_ID", socket.gethostname()),
        max_body_bytes=_integer("KRAVEL_MAX_BODY_BYTES", 1024 * 1024),
        api_token=os.getenv("KRAVEL_API_TOKEN", ""),
        default_namespace=os.getenv("KRAVEL_DEFAULT_NAMESPACE", "kravel-demo"),
        llm_base_url=os.getenv("KRAVEL_LLM_BASE_URL", "http://model-runner.docker.internal/engines/v1"),
        llm_model=os.getenv("KRAVEL_LLM_MODEL", "ai/qwen3:4b-instruct-2507-q4_K_M"),
        llm_api_key=os.getenv("KRAVEL_LLM_API_KEY", ""),
        llm_max_turns=_integer("KRAVEL_LLM_MAX_TURNS", 4),
        llm_reasoning_budget=_integer("KRAVEL_LLM_REASONING_BUDGET", 0),
        llm_timeout_seconds=_integer("KRAVEL_LLM_TIMEOUT_SECONDS", 90),
        mlflow_url=os.getenv("KRAVEL_MLFLOW_URL", "http://kravel-mlflow.kravel-observability.svc.cluster.local:5000"),
        mlflow_experiment=os.getenv("KRAVEL_MLFLOW_EXPERIMENT", "Kravel Guarded Debugger"),
        broker_url=os.getenv("KRAVEL_BROKER_URL", "http://kravel-approval-broker.kravel-system.svc.cluster.local:8090"),
        approval_timeout_seconds=_integer("KRAVEL_APPROVAL_TIMEOUT_SECONDS", 300),
        approval_token=os.getenv("KRAVEL_APPROVAL_TOKEN", ""),
        slack_bot_token=os.getenv("SLACK_BOT_TOKEN", ""),
        slack_channel_id=os.getenv("SLACK_CHANNEL_ID", ""),
        operator_token=os.getenv("KRAVEL_OPERATOR_TOKEN", ""),
        operator_url=os.getenv("KRAVEL_OPERATOR_URL", ""),
        kube=KubeConfig(
            host=os.getenv("KUBERNETES_SERVICE_HOST", ""),
            port=_integer("KUBERNETES_SERVICE_PORT_HTTPS", 443),
            token_path=os.getenv("KRAVEL_KUBE_TOKEN_PATH", "/var/run/secrets/kubernetes.io/serviceaccount/token"),
            ca_path=os.getenv("KRAVEL_KUBE_CA_PATH", "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"),
        ),
    )
