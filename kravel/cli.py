from __future__ import annotations

import argparse
import json
import time
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
    evaluate = sub.add_parser("evaluate", help="enqueue local MLflow evaluation of a completed Karl answer")
    evaluate.add_argument("--profile", choices=["quick", "all"], default="quick")
    evaluate.add_argument("--run-id", default="")
    evaluate.add_argument("--wait", action="store_true")
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
    elif args.command == "evaluate":
        base = f"http://127.0.0.1:{config.port}"
        headers = {"Content-Type": "application/json"}
        if config.api_token:
            headers["Authorization"] = "Bearer " + config.api_token
        def request(path, body=None):
            with urllib.request.urlopen(urllib.request.Request(base + path, headers=headers,
                data=json.dumps(body).encode() if body is not None else None), timeout=15) as response:
                return json.load(response)
        run_id = args.run_id
        if not run_id:
            session = request("/v1/demo-session")["session"]
            run_id = next((r["id"] for r in request("/v1/investigations")["runs"]
                if r["status"] == "completed" and r["started_at"] >= session["startedAt"]), "")
        if not run_id:
            raise SystemExit("Ask Karl a question first; no completed answer exists in this demo session.")
        job = request("/v1/evaluations", {"runId": run_id, "profile": args.profile})
        print("Local evaluation job: " + job["id"], flush=True)
        if args.wait:
            deadline = time.monotonic()+900
            while job["status"] in {"queued", "running"} and time.monotonic() < deadline:
                time.sleep(3)
                job = request("/v1/evaluations/" + job["id"])
        print(json.dumps(job, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
