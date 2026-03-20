from __future__ import annotations
import hashlib
import hmac
import json
import logging
import httpx
from fastapi import Request
from controller.models import TaskRequest, AgentResult, Thread
from controller.integrations.thread_id import derive_thread_id

logger = logging.getLogger(__name__)

# Default team-to-repo mapping (override via config)
DEFAULT_TEAM_REPO_MAP: dict[str, tuple[str, str]] = {}

class LinearIntegration:
    name = "linear"

    def __init__(self, webhook_secret: str, api_key: str, team_repo_map: dict[str, tuple[str, str]] | None = None):
        self._webhook_secret = webhook_secret
        self._api_key = api_key
        self._team_repo_map = team_repo_map or DEFAULT_TEAM_REPO_MAP
        self._client = httpx.AsyncClient(
            base_url="https://api.linear.app",
            headers={"Authorization": api_key, "Content-Type": "application/json"},
        )

    def _verify_signature(self, body: bytes, signature: str) -> bool:
        expected = hmac.new(self._webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    async def parse_webhook(self, request: Request) -> TaskRequest | None:
        body = await request.body()
        signature = request.headers.get("linear-signature", "")

        if not self._verify_signature(body, signature):
            logger.warning("Invalid Linear signature")
            return None

        payload = json.loads(body)
        action = payload.get("action")
        event_type = payload.get("type")

        # Only handle Comment create events
        if event_type != "Comment" or action != "create":
            return None

        data = payload.get("data", {})
        comment_body = data.get("body", "")
        issue = data.get("issue", {})
        issue_id = issue.get("identifier", "")
        issue_title = issue.get("title", "")
        team = issue.get("team", {})
        team_key = team.get("key", "")

        # Filter bot-generated comments
        user = data.get("user", {})
        if user.get("isBot") or not comment_body:
            return None

        # Derive thread ID
        thread_id = derive_thread_id("linear", issue_id=issue_id)

        # Map team to repo
        repo_owner, repo_name = self._team_repo_map.get(team_key, ("", ""))

        return TaskRequest(
            thread_id=thread_id,
            source="linear",
            source_ref={"issue_id": issue_id, "issue_title": issue_title, "team_key": team_key},
            repo_owner=repo_owner,
            repo_name=repo_name,
            task=f"[{issue_id}] {issue_title}\n\n{comment_body}",
        )

    async def fetch_context(self, thread: Thread) -> str:
        issue_id = thread.source_ref.get("issue_id", "")
        if not issue_id:
            return ""
        try:
            query = """
            query($id: String!) {
                issue(id: $id) {
                    description
                    comments { nodes { body user { name } } }
                }
            }
            """
            resp = await self._client.post("/graphql", json={"query": query, "variables": {"id": issue_id}})
            data = resp.json()
            issue = data.get("data", {}).get("issue", {})
            parts = [issue.get("description", "")]
            for comment in issue.get("comments", {}).get("nodes", []):
                user_name = comment.get("user", {}).get("name", "unknown")
                parts.append(f"{user_name}: {comment.get('body', '')}")
            return "\n\n".join(parts)
        except Exception:
            logger.exception("Failed to fetch Linear context")
            return ""

    async def report_result(self, thread: Thread, result: AgentResult) -> None:
        issue_id = thread.source_ref.get("issue_id", "")
        if not issue_id:
            return

        if result.commit_count > 0 and result.pr_url:
            body = f"**Pull Request Created**\n{result.pr_url}\n({result.commit_count} commits on `{result.branch}`)"
        elif result.commit_count > 0:
            body = f"Pushed {result.commit_count} commits to `{result.branch}`"
        elif result.exit_code != 0:
            body = f"Agent failed (exit code {result.exit_code})"
        else:
            body = "Agent completed but made no changes."

        mutation = """
        mutation($issueId: String!, $body: String!) {
            commentCreate(input: { issueId: $issueId, body: $body }) {
                success
            }
        }
        """
        await self._client.post("/graphql", json={
            "query": mutation,
            "variables": {"issueId": issue_id, "body": body},
        })

    async def acknowledge(self, request: Request) -> None:
        # Linear doesn't have an equivalent to Slack's emoji reactions
        pass
