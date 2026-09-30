from __future__ import annotations

import json
import logging
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .utils import safe_service_url, to_iso


class EmbeddingClient:
    def __init__(self, url="", model="", api_key=""):
        self.url, self.model, self.api_key = url, model, api_key

    @property
    def enabled(self):
        return bool(self.url)

    def embed(self, value):
        if not self.enabled:
            return [None for _ in value] if isinstance(value, list) else None
        values = value if isinstance(value, list) else [value]
        headers = {"content-type": "application/json", "user-agent": "kravel/0.2.0"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"
        body = json.dumps({"input": values, **({"model": self.model} if self.model else {})}).encode()
        with urllib.request.urlopen(urllib.request.Request(safe_service_url(self.url, "embedding"), data=body, headers=headers), timeout=30) as response:
            payload = json.load(response)
        embeddings = [item["embedding"] for item in sorted(payload.get("data", []), key=lambda item: item["index"])]
        if len(embeddings) != len(values):
            raise RuntimeError("Embedding service returned an unexpected response")
        return embeddings if isinstance(value, list) else embeddings[0]


class PrometheusSampler:
    def __init__(self, *, url, interval_ms, queries, cluster_id, store):
        self.url = url.rstrip("/") if url else ""
        self.interval = max(interval_ms / 1000, 1)
        self.queries, self.cluster_id, self.store = queries, cluster_id, store
        self.stop_event = threading.Event()
        self.thread = None

    def sample_once(self):
        sampled = 0
        for name, query in self.queries.items():
            endpoint = f"{safe_service_url(self.url, 'Prometheus')}/api/v1/query?{urllib.parse.urlencode({'query': query})}"
            with urllib.request.urlopen(endpoint, timeout=15) as response:
                payload = json.load(response)
            if payload.get("status") != "success":
                raise RuntimeError(f"Prometheus query {name} failed")
            for result in payload.get("data", {}).get("result", []):
                timestamp, value = result.get("value", [None, None])
                if timestamp is None:
                    continue
                outcome = self.store.record_metric_sample(self.cluster_id, name, to_iso(datetime.fromtimestamp(float(timestamp), timezone.utc)), value, result.get("metric", {}))
                sampled += int(outcome.get("inserted", False))
        return sampled

    def _run(self):
        while not self.stop_event.is_set():
            try:
                if self.url:
                    self.sample_once()
            except Exception:
                logging.exception("Prometheus sampling failed")
            self.stop_event.wait(self.interval)

    def start(self):
        if self.url and not self.thread:
            self.thread = threading.Thread(target=self._run, name="prometheus-sampler", daemon=True)
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=3)
