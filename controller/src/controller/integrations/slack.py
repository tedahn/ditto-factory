from __future__ import annotations
import hashlib
import hmac
import json
import time
import logging
import httpx
from fastapi import Request
from controller.models import TaskRequest, AgentResult, Thread
from controller.integrations.thread_id import derive_thread_id

logger = logging.getLogger(__name__)

class SlackIntegration:
    name = "slack"

    def __init__(self, signing_secret: str, bot_token: str, bot_user_id: str = ""):
        self._signing_secret = signing_secret
        self._bot_token = bot_token
        self._bot_user_id = bot_user_id
        self._client = httpx.AsyncClient(
            base_url="https://slack.com/api",
            headers={"Authorization": f"Bearer {bot_token}"},
        )

    def _verify_signature(self, body: bytes, timestamp: str, signature: str) -> bool:
        if abs(time.time() - float(timestamp)) > 300:
            return False
        sig_basestring = f"v0:{timestamp}:{body.decode()}"
        expected = "v0=" + hmac.new(
            self._signing_secret.encode(), sig_basestring.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    def _is_bot_message(self, event: dict) -> bool:
        if event.get("bot_id"):
            return True
        if self._bot_user_id and event.get("user") == self._bot_user_id:
            return True
        return False

    async def parse_webhook(self, request: Request) -> TaskRequest | None:
        body = await request.body()
        timestamp = request.headers.get("x-slack-request-timestamp", "")
        signature = request.headers.get("x-slack-signature", "")

        if not self._verify_signature(body, timestamp, signature):
            logger.warning("Invalid Slack signature")
            return None

        payload = json.loads(body)

        # URL verification challenge
        if payload.get("type") == "url_verification":
            return None

        event = payload.get("event", {})
        if event.get("type") not in ("message", "app_mention"):
            return None

        if self._is_bot_message(event):
            return None

        channel_id = event.get("channel", "")
        thread_ts = event.get("thread_ts") or event.get("ts", "")
        text = event.get("text", "")

        # Remove bot mention from text
        if self._bot_user_id:
            text = text.replace(f"<@{self._bot_user_id}>", "").strip()

        if not text:
            return None

        thread_id = derive_thread_id("slack", channel_id=channel_id, thread_ts=thread_ts)

        return TaskRequest(
            thread_id=thread_id,
            source="slack",
            source_ref={"channel": channel_id, "thread_ts": thread_ts, "ts": event.get("ts", "")},
            repo_owner="",  # resolved later by orchestrator
            repo_name="",
            task=text,
        )

    async def fetch_context(self, thread: Thread) -> str:
        channel = thread.source_ref.get("channel", "")
        thread_ts = thread.source_ref.get("thread_ts", "")
        if not channel or not thread_ts:
            return ""
        try:
            resp = await self._client.get(
                "/conversations.replies",
                params={"channel": channel, "ts": thread_ts, "limit": 50},
            )
            data = resp.json()
            if not data.get("ok"):
                return ""
            messages = data.get("messages", [])
            return "\n".join(m.get("text", "") for m in messages)
        except Exception:
            logger.exception("Failed to fetch Slack context")
            return ""

    async def report_result(self, thread: Thread, result: AgentResult) -> None:
        channel = thread.source_ref.get("channel", "")
        thread_ts = thread.source_ref.get("thread_ts", "")
        if not channel:
            return

        if result.commit_count > 0 and result.pr_url:
            text = f"**Pull Request Created**\n{result.pr_url}\n({result.commit_count} commits on `{result.branch}`)"
        elif result.commit_count > 0:
            text = f"Pushed {result.commit_count} commits to `{result.branch}`"
        elif result.exit_code != 0:
            text = f"Agent failed (exit code {result.exit_code})"
            if result.stderr:
                text += f"\n```\n{result.stderr[:500]}\n```"
        else:
            text = "Agent completed but made no changes."
            if result.stderr:
                text += f"\n{result.stderr}"

        await self._client.post("/chat.postMessage", json={
            "channel": channel,
            "thread_ts": thread_ts,
            "text": text,
        })

    async def acknowledge(self, request: Request) -> None:
        body = await request.body()
        payload = json.loads(body)
        event = payload.get("event", {})
        channel = event.get("channel", "")
        ts = event.get("ts", "")
        if channel and ts:
            await self._client.post("/reactions.add", json={
                "channel": channel,
                "timestamp": ts,
                "name": "eyes",
            })
