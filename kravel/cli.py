from __future__ import annotations

import argparse
import json
import urllib.request

from .config import load_config
from .kube import KubernetesClient
from .tools import discover_issues


def _parser():
    parser = argparse.ArgumentParser(prog="kravel")
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="print a deterministic live-cluster diagnosis")
    inspect.add_argument("--namespace", default="kravel-demo")
    sub.add_parser("check-llm", help="check Docker Model Runner connectivity")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    config = load_config()
    if args.command == "inspect":
        print(json.dumps(discover_issues(KubernetesClient(config.kube), args.namespace), indent=2))
    elif args.command == "check-llm":
        endpoint = config.llm_base_url.rstrip("/") + "/models"
        with urllib.request.urlopen(endpoint, timeout=10) as response:
            payload = json.load(response)
        print(f"Qwen endpoint reachable ({len(payload.get('data', []))} models visible)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
