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
