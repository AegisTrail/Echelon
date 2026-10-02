"""GitHub fetching: raw content (ETag-aware) + REST API (token, commits)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

import net
from _version import __version__

USER_AGENT = f"Echelon/{__version__} (+https://github.com/AegisTrail/Echelon)"


@dataclass
class ParsedGitHubURL:
    owner: str
    repo: str
    branch: str
    file_path: str
    start_line: int
    end_line: int
    file_url: str


@dataclass
class FetchResult:
    content: str
    etag: str = ""
    not_modified: bool = False


@dataclass
class CommitInfo:
    sha: str = ""
    url: str = ""
    message: str = ""
    author: str = ""
    date: str = ""


class GitHubError(RuntimeError):
    pass


class GitHubClient:
    GITHUB_RAW_BASE = "https://raw.githubusercontent.com"
    GITHUB_API_BASE = "https://api.github.com"

    def __init__(self, token: str | None = None, timeout: int = 15):
        self.token = (token or "").strip()
        self.timeout = timeout

    # ---------- URL handling ----------

    def parse_github_url(self, url: str) -> ParsedGitHubURL:
        from models import ConfigError  # deferred: keep import graph clean

        url = (url or "").strip()
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
            raise ConfigError(f"Invalid GitHub URL (must be https://github.com/...): {url!r}")
        path_parts = parsed.path.strip("/").split("/")
        if len(path_parts) < 2:
            raise ConfigError(f"Invalid GitHub URL: {url}")
        owner, repo = path_parts[0], path_parts[1]
        if not re.fullmatch(r"[A-Za-z0-9_.\-]+", owner) or not re.fullmatch(
            r"[A-Za-z0-9_.\-]+", repo
        ):
            raise ConfigError(f"Invalid owner/repo in URL: {url}")
        branch = "main"
        idx = 2
        if len(path_parts) >= 4 and path_parts[2] == "blob":
            branch = path_parts[3]
            idx = 4
        if not re.fullmatch(r"[A-Za-z0-9_./\-]+", branch) or ".." in branch:
            raise ConfigError(f"Invalid branch in URL: {url}")
        file_path = "/".join(path_parts[idx:])
        if not file_path or ".." in file_path.split("/"):
            raise ConfigError(f"Could not determine file path from URL: {url}")
        fragment = parsed.fragment
        if not fragment:
            raise ConfigError("URL must include line fragment, e.g. #L26-L31")
        match = re.match(r"L(\d+)(?:-L?(\d+))?", fragment)
        if not match:
            raise ConfigError(f"Invalid line fragment in URL: {fragment}")
        start_line = int(match.group(1))
        end_line = int(match.group(2) or match.group(1))
        if start_line < 1 or end_line < start_line:
            raise ConfigError(f"Invalid line range in URL: {fragment}")
        if end_line - start_line + 1 > 500:
            raise ConfigError("Line range too large (max 500 lines)")
        return ParsedGitHubURL(
            owner=owner,
            repo=repo,
            branch=branch,
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            file_url=url,
        )

    def build_raw_url(self, parsed: ParsedGitHubURL) -> str:
        return f"{self.GITHUB_RAW_BASE}/{parsed.owner}/{parsed.repo}/{parsed.branch}/{parsed.file_path}"

    # ---------- HTTP helpers ----------

    def _headers(self, extra: dict | None = None) -> dict:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if extra:
            headers.update(extra)
        return headers

    # ---------- raw content ----------

    def fetch_file_content(self, parsed: ParsedGitHubURL, etag: str = "") -> FetchResult:
        """Fetch raw file. Supports conditional GET via ETag (returns not_modified)."""
        raw_url = self.build_raw_url(parsed)
        headers = {"User-Agent": USER_AGENT}
        if self.token:
            # Raw host accepts the same token.
            headers["Authorization"] = f"Bearer {self.token}"
        if etag:
            headers["If-None-Match"] = etag
        try:
            resp = net.get(raw_url, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            raise GitHubError(
                f"Network error fetching {parsed.owner}/{parsed.repo}: {exc}"
            ) from exc
        if resp.status_code == 304:
            return FetchResult(content="", etag=etag, not_modified=True)
        if resp.status_code == 404:
            raise GitHubError(
                f"File not found: {parsed.owner}/{parsed.repo}@{parsed.branch}:{parsed.file_path} "
                "(renamed? branch deleted? private repo without token?)"
            )
        if resp.status_code in (401, 403):
            hint = (
                " (set ECHELON_GITHUB_TOKEN for private repos / higher rate limits)"
                if not self.token
                else ""
            )
            raise GitHubError(
                f"GitHub refused request ({resp.status_code}){hint}: {resp.text[:200]}"
            )
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise GitHubError(f"GitHub fetch failed: {exc}") from exc
        new_etag = resp.headers.get("ETag", "") or ""
        return FetchResult(content=resp.text, etag=new_etag, not_modified=False)

    # Backwards-compat shim: old code called fetch_file_content() expecting str.
    # New code uses fetch_file_content_result(); keep both working.
    def fetch_file_content_text(self, parsed: ParsedGitHubURL) -> str:
        return self.fetch_file_content(parsed).content

    def extract_lines(self, content: str, start_line: int, end_line: int) -> str:
        lines = content.splitlines()
        if start_line < 1 or end_line < start_line:
            raise GitHubError(f"Invalid line range L{start_line}-L{end_line}")
        if end_line > len(lines):
            raise GitHubError(
                f"Requested lines L{start_line}-L{end_line} out of range (file has {len(lines)} lines). "
                "File likely shrank; check for deletions above."
            )
        snippet_lines = lines[start_line - 1 : end_line]
        return "\n".join(snippet_lines) + ("\n" if snippet_lines else "")

    # ---------- REST API: latest commit touching a path ----------

    def get_latest_commit(
        self, owner: str, repo: str, branch: str, file_path: str
    ) -> CommitInfo | None:
        """Return newest commit for *file_path* on *branch* (None on any failure)."""
        url = f"{self.GITHUB_API_BASE}/repos/{owner}/{repo}/commits"
        params = {"path": file_path, "sha": branch, "per_page": 1}
        try:
            resp = net.get(url, headers=self._headers(), params=params, timeout=self.timeout)
        except requests.RequestException:
            return None
        if resp.status_code == 404:
            return None
        if resp.status_code in (401, 403):
            return None  # rate-limited or private without token: non-fatal
        try:
            resp.raise_for_status()
        except requests.HTTPError:
            return None
        try:
            items = resp.json()
        except ValueError:
            return None
        if not items:
            return None
        item = items[0]
        sha = str(item.get("sha", ""))
        html_url = str(item.get("html_url", ""))
        commit = item.get("commit") or {}
        message = (
            str(commit.get("message", "")).splitlines()[0][:300] if commit.get("message") else ""
        )
        author = ""
        author_obj = commit.get("author") or {}
        if isinstance(author_obj, dict):
            author = str(author_obj.get("name", ""))
        date = str(author_obj.get("date", "")) if isinstance(author_obj, dict) else ""
        return CommitInfo(sha=sha, url=html_url, message=message, author=author, date=date)
