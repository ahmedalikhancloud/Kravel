from __future__ import annotations

import logging
import signal
import sys
import threading

from .api import create_server
from .background import EmbeddingClient, PrometheusSampler
from .cli import main as cli_main
from .config import load_config
from .mcp import run_mcp
from .store import TemporalStore
from .watcher import KubernetesWatcher


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv else "serve"
    if command in {"pipeline", "report", "probe-resource", "check-llm"}:
        return cli_main(argv)
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    config = load_config()
    store = TemporalStore(config.db_path)
    embedding = EmbeddingClient(config.embedding_url, config.embedding_model, config.embedding_api_key)
    watcher = KubernetesWatcher(kube=config.kube, resources=config.watch_resources, cluster_id=config.cluster_id, store=store)
    prometheus = PrometheusSampler(url=config.prometheus_url, interval_ms=config.prometheus_interval_ms, queries=config.prometheus_queries, cluster_id=config.cluster_id, store=store)
    if command == "mcp":
        try:
            run_mcp(store, config)
        finally:
            store.close()
        return 0
    if command not in {"serve", "serve-watch", "watch"}:
        raise SystemExit(f"Unknown command: {command}")
    server = create_server(store, config, embedding) if command in {"serve", "serve-watch"} else None
    def shutdown(_signum=None, _frame=None):
        watcher.stop_event.set(); prometheus.stop_event.set()
        if server:
            threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        prometheus.start()
        if command in {"serve-watch", "watch"}:
            watcher.start()
        if server:
            logging.info("API listening on %s:%s", config.host, config.port)
            server.serve_forever()
        else:
            signal.pause()
    finally:
        watcher.stop(); prometheus.stop(); store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
