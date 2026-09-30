from __future__ import annotations

import logging
import signal
import sys
import threading

from .api import create_server
from .broker import ApprovalBroker, create_broker_server
from .cli import main as cli_main
from .config import load_config
from .kube import KubernetesClient
from .store import AuditStore
from .operator import OperatorConsole, create_operator_server


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv else "serve"
    if command in {"check-llm", "inspect"}:
        return cli_main(argv)
    if command not in {"serve", "broker", "operator"}:
        raise SystemExit(f"Unknown command: {command}")
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
    config = load_config()
    store = AuditStore(config.db_path)
    kube = KubernetesClient(config.kube)
    server = create_server(store, config, kube) if command == "serve" else create_operator_server(OperatorConsole(kube, store), config) if command == "operator" else create_broker_server(ApprovalBroker(kube, store, config), config)

    def shutdown(_signum=None, _frame=None):
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        logging.info("%s listening on %s:%s", command, config.host, config.port)
        if hasattr(server, "workflows"):
            server.workflows.start_observer()
        server.serve_forever()
    finally:
        if hasattr(server, "workflows"):
            server.workflows.stop.set()
        server.server_close()
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
