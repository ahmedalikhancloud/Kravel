"""Credential-free proposal submission. There is deliberately no decision method."""
import json
import urllib.error
import urllib.request

from .guardrails import public_evidence
from .utils import safe_service_url


class BrokerRequestError(RuntimeError):
    """A bounded, redacted broker rejection; never includes its private URL."""
    def __init__(self, status, payload):
        self.status = status
        safe = public_evidence(payload if isinstance(payload, dict) else {})
        self.details = {k: safe[k] for k in ("errorCode", "validation") if k in safe}
        # A generic 400/500 can occur AFTER persistence. Only explicit broker
        # validation evidence (or an authorization/not-found rejection) proves
        # no review was created. Do not convert an uncertain delivery into a retry.
        self.confirmed_rejected = safe.get("reviewCreated") is False or status in {401, 403, 404}
        message = safe.get("error")
        super().__init__(message[:1500] if isinstance(message, str) and message.strip() else f"Approval service returned HTTP {status}. Check the approval inbox before retrying.")


def broker_call(config, path, payload=None, *, timeout=20, method="POST"):
    url = safe_service_url(config.broker_url, "approval broker") + path
    request = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None, method=method,
        headers={"Content-Type": "application/json", "User-Agent": "kravel/0.3.0"})
    # Never retry: a lost response may already have created a review request.
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read(65536))
        except (ValueError, UnicodeError):
            payload = {}
        raise BrokerRequestError(exc.code, payload) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise RuntimeError("Approval service response is unavailable. Check the approval inbox before retrying; the request was not retried.") from None


def request_approval(config, payload):
    return broker_call(config, "/v1/proposals", payload, timeout=120 if "plan" in payload else 20)
