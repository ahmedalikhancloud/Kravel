FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/models/huggingface \
    TORCH_HOME=/models/torch \
    LAYA_HOST=0.0.0.0 \
    LAYA_PORT=8000 \
    LAYA_DEVICE=cpu \
    LAYA_PRELOAD=1 \
    LAYA_MODELS=english \
    LAYA_THREADS=4

RUN python -m pip install --no-cache-dir --disable-pip-version-check "laya[serve]==0.3.21" \
    && mkdir -p /models \
    && chown -R 1000:1000 /models

USER 1000:1000
EXPOSE 8000
CMD ["laya-serve"]
