"""GitHub API client for fetching repo metadata, file trees, and file contents."""

from __future__ import annotations

import base64
import re
from urllib.parse import unquote

import httpx


class GitHubError(Exception):
    """Base exception for GitHub API errors."""

    def __init__(self, message: str, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(message)


class GitHubNotFoundError(GitHubError):
    """Raised when a GitHub resource is not found (404)."""

    def __init__(self, message: str = "Resource not found"):
        super().__init__(message, status_code=404)


class GitHubRateLimitError(GitHubError):
    """Raised when GitHub API rate limit is exceeded (403)."""

    def __init__(self, message: str = "GitHub API rate limit exceeded"):
        super().__init__(message, status_code=403)


class GitHubClient:
    """Async GitHub API client using httpx.

    Args:
        token: Optional GitHub personal access token for authentication.
    """

    BASE_URL = "https://api.github.com"

    def __init__(self, token: str | None = None):
        self.token = token
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "ditto-factory",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"

        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            headers=headers,
            timeout=30.0,
        )

    async def __aenter__(self) -> GitHubClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Execute an HTTP request with standard error handling."""
        response = await self._client.request(method, url, **kwargs)

        if response.status_code == 404:
            raise GitHubNotFoundError(
                f"GitHub resource not found: {method} {url}"
            )
        if response.status_code == 403:
            msg = "GitHub API rate limit exceeded"
            try:
                body = response.json()
                if "message" in body:
                    msg = body["message"]
            except Exception:
                pass
            raise GitHubRateLimitError(msg)
        if response.status_code >= 400:
            try:
                body = response.json()
                detail = body.get("message", response.text)
            except Exception:
                detail = response.text
            raise GitHubError(
                f"GitHub API error {response.status_code}: {detail}",
                status_code=response.status_code,
            )

        return response

    async def _get(self, url: str, **kwargs) -> httpx.Response:
        return await self._request("GET", url, **kwargs)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    @staticmethod
    def parse_github_url(url: str) -> dict:
        """Parse a GitHub URL into its components.

        Handles formats:
            - https://github.com/owner/repo
            - https://github.com/owner/repo.git
            - https://github.com/owner/repo/tree/branch/path
            - https://github.com/owner/repo/blob/branch/path
            - github.com/owner/repo (without scheme)

        Returns:
            dict with keys: owner, repo, branch, path
        """
        url = url.strip().rstrip("/")

        # Remove .git suffix
        if url.endswith(".git"):
            url = url[:-4]

        # Add scheme if missing
        if not url.startswith("http"):
            url = "https://" + url

        # Parse the URL path
        pattern = re.compile(
            r"https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)"
            r"(?:/(?:tree|blob)/(?P<branch>[^/]+)(?:/(?P<path>.+))?)?"
        )
        match = pattern.match(url)
        if not match:
            raise ValueError(f"Invalid GitHub URL: {url}")

        owner = unquote(match.group("owner"))
        repo = unquote(match.group("repo"))
        branch = match.group("branch") or "main"
        path = match.group("path") or ""

        # Clean up path
        path = path.strip("/")

        return {
            "owner": owner,
            "repo": repo,
            "branch": branch,
            "path": path,
        }

    async def get_repo_info(self, owner: str, repo: str) -> dict:
        """Fetch repository metadata.

        Returns:
            dict with: description, default_branch, stargazers_count,
                       language, topics
        """
        resp = await self._get(f"/repos/{owner}/{repo}")
        data = resp.json()
        return {
            "description": data.get("description"),
            "default_branch": data.get("default_branch", "main"),
            "stargazers_count": data.get("stargazers_count", 0),
            "language": data.get("language"),
            "topics": data.get("topics", []),
        }

    async def get_tree(
        self, owner: str, repo: str, branch: str = "main"
    ) -> list[dict]:
        """Fetch the full recursive file tree for a branch.

        Returns:
            List of dicts with: path, type ("blob" or "tree"), size
        """
        resp = await self._get(
            f"/repos/{owner}/{repo}/git/trees/{branch}",
            params={"recursive": "true"},
        )
        data = resp.json()
        tree = data.get("tree", [])
        return [
            {
                "path": item["path"],
                "type": item["type"],
                "size": item.get("size"),
            }
            for item in tree
        ]

    async def get_file_content(
        self, owner: str, repo: str, path: str, ref: str = "main"
    ) -> str:
        """Fetch and decode the content of a single file.

        For files > 1 MB, automatically falls back to the blob API.

        Returns:
            The file content as a decoded UTF-8 string.
        """
        resp = await self._get(
            f"/repos/{owner}/{repo}/contents/{path}",
            params={"ref": ref},
        )
        data = resp.json()

        # If the content is present and base64-encoded, decode it directly
        if data.get("content") and data.get("encoding") == "base64":
            return base64.b64decode(data["content"]).decode("utf-8")

        # For large files (>1 MB), the contents API returns size but no
        # content. Fall back to the blob API using the sha.
        if data.get("sha"):
            return await self._get_blob_content(owner, repo, data["sha"])

        raise GitHubError(
            f"Unable to retrieve content for {path}: unexpected response format"
        )

    async def _get_blob_content(
        self, owner: str, repo: str, sha: str
    ) -> str:
        """Fetch file content via the blob API (supports files > 1 MB)."""
        resp = await self._get(f"/repos/{owner}/{repo}/git/blobs/{sha}")
        data = resp.json()

        if data.get("encoding") == "base64":
            return base64.b64decode(data["content"]).decode("utf-8")

        # utf-8 encoding returned directly
        return data.get("content", "")

    async def get_latest_commit(
        self, owner: str, repo: str, branch: str = "main"
    ) -> dict:
        """Fetch the latest commit on a branch.

        Returns:
            dict with: sha, date, message
        """
        resp = await self._get(f"/repos/{owner}/{repo}/commits/{branch}")
        data = resp.json()
        commit = data.get("commit", {})
        return {
            "sha": data.get("sha", ""),
            "date": commit.get("committer", {}).get("date", ""),
            "message": commit.get("message", ""),
        }
