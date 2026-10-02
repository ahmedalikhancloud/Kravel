from __future__ import annotations

import json
import urllib.parse
import urllib.request


class SlackApprovalClient:
    """Outbound-only Slack adapter: post a request, then poll emoji reactions."""

    def __init__(self, token: str, channel_id: str):
        self.token = token
        self.channel_id = channel_id
        self._bot_user_id = ""

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.channel_id)

    def _call(self, method: str, payload: dict) -> dict:
        request = urllib.request.Request(
            f"https://slack.com/api/{method}",
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json; charset=utf-8", "User-Agent": "kravel/0.3.0"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.load(response)
        if not result.get("ok"):
            raise RuntimeError(f"Slack {method} failed: {result.get('error', 'unknown_error')}")
        return result

    def bot_user_id(self) -> str:
        if not self._bot_user_id:
            self._bot_user_id = str(self._call("auth.test", {}).get("user_id", ""))
        return self._bot_user_id

    def post(self, proposal: dict) -> dict:
        if proposal["fix_id"].startswith("plan-"):
            # Slack's text limits must never silently hide part of approved code.
            root = self._call("chat.postMessage", {"channel": self.channel_id, "text": f"Preparing complete review {proposal['id'][:8]}. DO NOT APPROVE until this message says READY.", "unfurl_links": False, "unfurl_media": False})
            draft = proposal["dryRun"][0]["reviewedPlan"]
            sections = [("Exact ordered commands", proposal["command"])]
            sections += [(f"Generated file: {name}", source) for name, source in draft["plan"]["files"].items()]
            sections += [("Validation results & limitations", json.dumps([{k: v for k, v in r.items() if k != "reviewedPlan"} for r in proposal["dryRun"]], indent=2))]
            for title, source in sections:
                for offset in range(0, len(source), 2800):
                    self._call("chat.postMessage", {"channel": root["channel"], "thread_ts": root["ts"], "text": f"{title} · part {offset//2800+1}\n" + source[offset:offset+2800], "unfurl_links": False, "unfurl_media": False})
            limited = any(r["validation"] in {"deferred", "not_available"} for r in proposal["dryRun"])
            # A distinct message created AFTER all review chunks prevents an
            # early reaction on the placeholder from becoming valid approval.
            ready = self._call("chat.postMessage", {"channel": root["channel"], "thread_ts": root["ts"], "text": f"READY for human approval · {draft['plan']['title']}\nPlan hash: {draft['planHash']}\nRead ALL commands, generated files and validation results in this thread. Cluster-admin execution: namespaces, RBAC, storage and node changes can damage the cluster.\n" + ("WARNING: Some commands lack a passed server dry-run. 👍 explicitly accepts the listed limitations.\n" if limited else "Server dry-runs passed for supported mutations.\n") + f"React on THIS READY message with 👍 to approve this exact plan, or ✕ to reject. Deadline: {proposal['expires_at']}. No automatic follow-up changes.", "unfurl_links": False, "unfurl_media": False})
            self.update(root["channel"], root["ts"], f"Review complete · {draft['plan']['title']} · {proposal['id'][:8]}. Open this thread, inspect all code/commands/validation, then react only on its final READY message. Reactions on this summary do not approve anything.")
            return {"channel": ready["channel"], "ts": ready["ts"]}
        text = (
            f"Kravel approval requested ({proposal['id'][:8]})\n"
            f"Fix: {proposal['fix_id']} · {proposal['resource']}\n"
            f"Command: `{proposal['command']}`\n"
            f"Server dry-run output: ```{json.dumps(proposal['dryRun'], indent=2)[:5000]}```\n"
            f"Dry run passed. React with :thumbsup: to approve or :x: to reject. Expires in 5 minutes."
        )
        result = self._call("chat.postMessage", {"channel": self.channel_id, "text": text, "unfurl_links": False, "unfurl_media": False})
        return {"channel": result["channel"], "ts": result["ts"]}

    def decision(self, channel: str, timestamp: str) -> tuple[str, str] | None:
        query = urllib.parse.urlencode({"channel": channel, "timestamp": timestamp, "full": "true"})
        request = urllib.request.Request(
            f"https://slack.com/api/reactions.get?{query}",
            headers={"Authorization": f"Bearer {self.token}", "User-Agent": "kravel/0.3.0"},
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.load(response)
        if not result.get("ok"):
            raise RuntimeError(f"Slack reactions.get failed: {result.get('error', 'unknown_error')}")
        bot = self.bot_user_id()
        for reaction in result.get("message", {}).get("reactions", []):
            humans = [user for user in reaction.get("users", []) if user != bot]
            if not humans:
                continue
            if reaction.get("name") in {"thumbsup", "+1", "white_check_mark"}:
                return "approved", humans[0]
            if reaction.get("name") in {"x", "thumbsdown", "-1"}:
                return "rejected", humans[0]
        return None

    def update(self, channel: str, timestamp: str, text: str):
        self._call("chat.update", {"channel": channel, "ts": timestamp, "text": text})
