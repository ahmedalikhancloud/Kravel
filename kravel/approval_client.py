"""Credential-free proposal submission. There is deliberately no decision method."""
import json
import urllib.request

from .utils import safe_service_url


def broker_call(config, path, payload, *, timeout=20):
    url = safe_service_url(config.broker_url, "approval broker") + path
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "kravel/0.3.0"})
    # Never retry: a lost response may already have created a review request.
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def request_approval(config, payload):
    return broker_call(config, "/v1/proposals", payload, timeout=120 if "plan" in payload else 20)
