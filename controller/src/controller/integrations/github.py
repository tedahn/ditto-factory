# src/controller/integrations/github.py
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

BOT_MARKERS = ["**Pull Request Created**", "**Agent Result**", "Pushed", "commits on `df/"]

class GitHubIntegration:
    name = "github"

    def __init__(self, webhook_secret: str, app_id: str = "", private_key: str = "",
                 allowed_orgs: list[str] | None = None,
                 client: httpx.AsyncClient | None = None):
        self._webhook_secret = webhook_secret
        self._app_id = app_id
        self._private_key = private_key
        self._allowed_orgs = allowed_orgs or []
        self._client = client or httpx.AsyncClient(headers={"Accept": "application/vnd.github+json"})

    def _verify_signature(self, body: bytes, signature: str) -> bool:
        expected = "sha256=" + hmac.new(self._webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def _is_bot_message(self, text: str) -> bool:
        return any(marker in text for marker in BOT_MARKERS)

    def _is_allowed_org(self, org: str) -> bool:
        if not self._allowed_orgs:
            return True
        return org in self._allowed_orgs

    async def parse_webhook(self, request: Request) -> TaskRequest | None:
        body = await request.body()
        signature = request.headers.get("x-hub-signature-256", "")
        event_type = request.headers.get("x-github-event", "")

        if not self._verify_signature(body, signature):
            logger.warning("Invalid GitHub signature")
            return None

        payload = json.loads(body)
        action = payload.get("action", "")
        repo = payload.get("repository", {})
        org = repo.get("owner", {}).get("login", "")
        repo_name = repo.get("name", "")

        if not self._is_allowed_org(org):
            logger.info("Org %s not in allowed list", org)
            return None

        if event_type == "issue_comment" and action == "created":
            return self._handle_issue_comment(payload, org, repo_name)
        elif event_type == "issues" and action == "opened":
            return self._handle_issue_opened(payload, org, repo_name)
        elif event_type == "pull_request_review_comment" and action == "created":
            return self._handle_pr_review_comment(payload, org, repo_name)
        elif event_type == "pull_request_review" and action == "submitted":
            return self._handle_pr_review(payload, org, repo_name)

        return None

    def _handle_issue_comment(self, payload: dict, org: str, repo_name: str) -> TaskRequest | None:
        comment = payload.get("comment", {})
        body = comment.get("body", "")
        issue = payload.get("issue", {})
        number = issue.get("number", 0)

        if self._is_bot_message(body):
            return None

        # Determine if this is on a PR or issue
        is_pr = "pull_request" in issue
        if is_pr:
            thread_id = derive_thread_id("github_pr", repo_owner=org, repo_name=repo_name, pr_number=number)
        else:
            thread_id = derive_thread_id("github_issue", repo_owner=org, repo_name=repo_name, issue_number=number)

        return TaskRequest(
            thread_id=thread_id,
            source="github",
            source_ref={"type": "issue_comment", "number": number, "is_pr": is_pr},
            repo_owner=org,
            repo_name=repo_name,
            task=body,
        )

    def _handle_issue_opened(self, payload: dict, org: str, repo_name: str) -> TaskRequest | None:
        issue = payload.get("issue", {})
        number = issue.get("number", 0)
        title = issue.get("title", "")
        body = issue.get("body", "") or ""

        if self._is_bot_message(body):
            return None

        thread_id = derive_thread_id("github_issue", repo_owner=org, repo_name=repo_name, issue_number=number)

        return TaskRequest(
            thread_id=thread_id,
            source="github",
            source_ref={"type": "issue_opened", "number": number},
            repo_owner=org,
            repo_name=repo_name,
            task=f"{title}\n\n{body}",
        )

    def _handle_pr_review_comment(self, payload: dict, org: str, repo_name: str) -> TaskRequest | None:
        comment = payload.get("comment", {})
        body = comment.get("body", "")
        pr = payload.get("pull_request", {})
        number = pr.get("number", 0)

        if self._is_bot_message(body):
            return None

        thread_id = derive_thread_id("github_pr", repo_owner=org, repo_name=repo_name, pr_number=number)

        return TaskRequest(
            thread_id=thread_id,
            source="github",
            source_ref={"type": "pr_review_comment", "number": number},
            repo_owner=org,
            repo_name=repo_name,
            task=body,
        )

    def _handle_pr_review(self, payload: dict, org: str, repo_name: str) -> TaskRequest | None:
        review = payload.get("review", {})
        body = review.get("body", "") or ""
        pr = payload.get("pull_request", {})
        number = pr.get("number", 0)

        if not body or self._is_bot_message(body):
            return None

        thread_id = derive_thread_id("github_pr", repo_owner=org, repo_name=repo_name, pr_number=number)

        return TaskRequest(
            thread_id=thread_id,
            source="github",
            source_ref={"type": "pr_review", "number": number},
            repo_owner=org,
            repo_name=repo_name,
            task=body,
        )

    async def fetch_context(self, thread: Thread) -> str:
        owner = thread.repo_owner
        repo = thread.repo_name
        number = thread.source_ref.get("number", 0)
        is_pr = thread.source_ref.get("is_pr", False)

        if is_pr:
            url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{number}/comments"
        else:
            url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}/comments"

        response = await self._client.get(url)
        if response.status_code != 200:
            logger.warning("Failed to fetch comments: %s %s", response.status_code, url)
            return ""

        comments = response.json()
        parts = []
        for comment in comments:
            user = comment.get("user", {}).get("login", "unknown")
            body = comment.get("body", "")
            parts.append(f"{user}: {body}")
        return "\n\n".join(parts)

    async def report_result(self, thread: Thread, result: AgentResult) -> None:
        owner = thread.repo_owner
        repo = thread.repo_name
        number = thread.source_ref.get("number", 0)

        if result.exit_code == 0:
            lines = ["**Agent Result**"]
            if result.pr_url:
                lines.append(f"Pull Request: {result.pr_url}")
            lines.append(f"Branch: `{result.branch}`")
            lines.append(f"Commits: {result.commit_count}")
            comment_body = "\n".join(lines)
        else:
            comment_body = f"**Agent Result** (failed)\n\nBranch: `{result.branch}`\n\n```\n{result.stderr}\n```"

        url = f"https://api.github.com/repos/{owner}/{repo}/issues/{number}/comments"
        response = await self._client.post(url, json={"body": comment_body})
        if response.status_code not in (200, 201):
            logger.warning("Failed to post comment: %s %s", response.status_code, url)

    async def create_pr(self, owner: str, repo: str, branch: str, title: str, body: str,
                        base: str = "main") -> str:
        url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
        response = await self._client.post(url, json={
            "title": title,
            "body": body,
            "head": branch,
            "base": base,
        })
        response.raise_for_status()
        return response.json().get("html_url", "")

    async def acknowledge(self, request: Request) -> None:
        # GitHub doesn't need immediate acknowledgment like Slack
        pass
