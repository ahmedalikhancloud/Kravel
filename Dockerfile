FROM registry.k8s.io/kubectl:v1.36.1 AS kubectl
FROM python:3.12-slim
COPY --from=kubectl /bin/kubectl /usr/local/bin/kubectl

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    KRAVEL_DB_PATH=/data/audit.db \
    KRAVEL_HOST=0.0.0.0 \
    KRAVEL_PORT=8080 \
    NEMO_GUARDRAILS_NO_USAGE_STATS=1 \
    HF_HUB_OFFLINE=1

WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir \
      'setuptools>=75' \
      langgraph==1.2.12 \
      mlflow-tracing==3.16.0 \
      openai==3.22.1 \
      nemoguardrails==0.24.1 \
      PyYAML==6.0.3
COPY kravel ./kravel
RUN useradd --uid 1000 --create-home --shell /usr/sbin/nologin kravel \
    && pip install --no-cache-dir --no-deps --no-build-isolation . \
    && mkdir -p /data \
    && chown -R kravel:kravel /app /data

USER kravel
EXPOSE 8080

CMD ["python", "-m", "kravel.main", "serve"]
