"""Route fetching + commit enrichment to the right provider.

Add-time branch disambiguation (:meth:`RemoteClient.resolve`) probes
candidate ``branch/path`` splits longest-branch-first, so branches containing
slashes work on every provider. All commit lookups are best-effort and return
``None`` instead of raising.
"""

from __future__ import annotations

import base64
from urllib.parse import quote

import requests

import net
from github_client import (
    USER_AGENT,
    CommitInfo,
    FetchResult,
    GitHubClient,
    GitHubError,
    ParsedGitHubURL,
)
from providers import ParsedRemote, is_github_cloud


class RemoteError(GitHubError):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def extract_lines(content: str, start_line: int, end_line: int) -> str:
    lines = (content or "").splitlines()
    if start_line < 1 or end_line < start_line:
        raise RemoteError(f"Invalid line range L{start_line}-L{end_line}")
    if end_line > len(lines):
        raise RemoteError(
            f"Requested lines L{start_line}-L{end_line} out of range (file has {len(lines)} lines). "
            "File likely shrank; check for deletions above."
        )
    chunk = lines[start_line - 1 : end_line]
    return "\n".join(chunk) + ("\n" if chunk else "")


class RemoteClient:
    def __init__(self, tokens: dict | None = None, timeout: int = 15):
        tokens = tokens or {}
        self.token = ""  # kept for compat with monitor's token handoff
        self.timeout = timeout
        self.github = GitHubClient(token=tokens.get("github_token", ""), timeout=timeout)
        self.github_token = (tokens.get("github_token") or "").strip()
        self.gitlab_token = (tokens.get("gitlab_token") or "").strip()
        self.gitea_token = (tokens.get("gitea_token") or "").strip()
        self.bb_user = (tokens.get("bitbucket_username") or "").strip()
        self.bb_app_password = (tokens.get("bitbucket_app_password") or "").strip()

    def update_tokens(self, tokens: dict | None) -> None:
        """Refresh credentials from reloaded config (daemon picks up changes)."""
        tokens = tokens or {}
        if tokens.get("github_token"):
            self.github_token = tokens["github_token"].strip()
            self.github.token = self.github_token
        if tokens.get("gitlab_token"):
            self.gitlab_token = tokens["gitlab_token"].strip()
        if tokens.get("gitea_token"):
            self.gitea_token = tokens["gitea_token"].strip()
        if tokens.get("bitbucket_username"):
            self.bb_user = tokens["bitbucket_username"].strip()
        if tokens.get("bitbucket_app_password"):
            self.bb_app_password = tokens["bitbucket_app_password"].strip()

    # ---------- low-level ----------

    def _get(
        self,
        url: str,
        *,
        headers: dict | None = None,
        params: dict | None = None,
        auth: tuple | None = None,
        hint: str = "",
    ) -> requests.Response:
        base = {"User-Agent": USER_AGENT}
        if headers:
            base.update(headers)
        try:
            return net.get(url, headers=base, params=params, auth=auth, timeout=self.timeout)
        except requests.RequestException as exc:
            raise RemoteError(f"Network error fetching {url[:120]}: {exc}. {hint}".strip()) from exc

    def _check(self, resp: requests.Response, *, what: str, private_hint: str = "") -> None:
        if resp.status_code == 404:
            raise RemoteError(
                f"File not found: {what} (renamed? branch deleted? {private_hint})".strip(),
                status=404,
            )
        if resp.status_code in (401, 403):
            raise RemoteError(
                f"Access denied ({resp.status_code}) for {what}. {private_hint}".strip(),
                status=resp.status_code,
            )
        if resp.status_code == 429:
            raise RemoteError(f"Rate limited (429) by {what}; retry later.", status=429)
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise RemoteError(f"Fetch failed for {what}: {exc}", status=resp.status_code) from exc

    # ---------- public API ----------

    def fetch(self, parsed: ParsedRemote, etag: str = "") -> FetchResult:
        """Fetch full file content for *parsed* (304-aware where supported)."""
        if parsed.provider == "github" and is_github_cloud(parsed):
            gh = ParsedGitHubURL(
                owner=parsed.owner,
                repo=parsed.repo,
                branch=parsed.branch,
                file_path=parsed.file_path,
                start_line=parsed.start_line,
                end_line=parsed.end_line,
                file_url=parsed.file_url,
            )
            if self.token and not self.github.token:
                self.github.token = self.token
            return self.github.fetch_file_content(gh, etag=etag or "")
        if parsed.provider == "github":
            content = self._github_enterprise_file(parsed)
        elif parsed.provider == "gitlab":
            content = self._gitlab_file(parsed.branch, parsed, parsed.file_path)
        elif parsed.provider == "gitea":
            content = self._gitea_file(parsed.branch, parsed, parsed.file_path)
        elif parsed.provider == "bitbucket":
            content = self._bitbucket_file(parsed.branch, parsed, parsed.file_path)
        elif parsed.provider == "sourcehut":
            content = self._sourcehut_file(parsed.branch, parsed, parsed.file_path)
        elif parsed.provider == "raw":
            content = self._raw_text_file(parsed)
        else:
            raise RemoteError(f"Unsupported provider: {parsed.provider}")
        return FetchResult(content=content, etag="", not_modified=False)

    def resolve(self, parsed: ParsedRemote) -> tuple[str, str, str]:
        """Resolve (branch, path, content), probing slash-branches longest-first."""
        if parsed.provider == "raw" or parsed.fixed_ref or not parsed.remainder:
            content = self.fetch(parsed).content
            return parsed.branch, parsed.file_path, content
        segments = parsed.remainder.split("/")
        # Longest branch first: "a/b/f.py" -> ("a/b", "f.py") before ("a", "b/f.py").
        for cut in range(len(segments) - 1, 0, -1):
            branch = "/".join(segments[:cut])
            path = "/".join(segments[cut:])
            try:
                content = self._candidate_content(parsed, branch, path)
            except RemoteError as exc:
                if exc.status == 404:
                    continue
                raise
            return branch, path, content
        raise RemoteError(
            f"Could not resolve branch/path for {parsed.file_url} "
            "(tried every candidate split; file not found under any branch prefix)"
        )

    def get_latest_commit(self, parsed: ParsedRemote) -> CommitInfo | None:
        """Newest commit touching the file, or None (never raises)."""
        try:
            if parsed.provider == "github":
                if is_github_cloud(parsed):
                    gh = ParsedGitHubURL(
                        owner=parsed.owner,
                        repo=parsed.repo,
                        branch=parsed.branch,
                        file_path=parsed.file_path,
                        start_line=parsed.start_line,
                        end_line=parsed.end_line,
                        file_url=parsed.file_url,
                    )
                    return self.github.get_latest_commit(gh.owner, gh.repo, gh.branch, gh.file_path)
                return self._ghe_commit(parsed)
            if parsed.provider == "gitlab":
                return self._gitlab_commit(parsed)
            if parsed.provider == "gitea":
                return self._gitea_commit(parsed)
            if parsed.provider == "bitbucket":
                return self._bitbucket_commit(parsed)
            return None  # sourcehut / raw: no supported API
        except Exception:
            return None

    # ---------- candidate probing ----------

    def _candidate_content(self, parsed: ParsedRemote, branch: str, path: str) -> str:
        if parsed.provider == "github" and is_github_cloud(parsed):
            url = f"https://raw.githubusercontent.com/{parsed.owner}/{parsed.repo}/{branch}/{path}"
            headers: dict = {}
            token = self.github.token or self.token
            if token:
                headers["Authorization"] = f"Bearer {token}"
            resp = self._get(url, headers=headers)
            self._check(resp, what=f"{parsed.owner}/{parsed.repo}@{branch}:{path}")
            return resp.text
        if parsed.provider == "github":
            return self._github_enterprise_file(parsed, branch=branch, path=path)
        if parsed.provider == "gitlab":
            return self._gitlab_file(branch, parsed, path)
        if parsed.provider == "gitea":
            return self._gitea_file(branch, parsed, path)
        if parsed.provider == "bitbucket":
            return self._bitbucket_file(branch, parsed, path)
        if parsed.provider == "sourcehut":
            return self._sourcehut_file(branch, parsed, path)
        raise RemoteError(f"Unsupported provider: {parsed.provider}")

    # ---------- GitHub Enterprise (API contents, base64) ----------

    def _ghe_headers(self) -> dict:
        headers = {"Accept": "application/vnd.github+json"}
        token = self.github_token or self.github.token or self.token
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _github_enterprise_file(
        self, parsed: ParsedRemote, *, branch: str = "", path: str = ""
    ) -> str:
        branch = branch or parsed.branch
        path = path or parsed.file_path
        url = f"https://{parsed.host}/api/v3/repos/{parsed.owner}/{parsed.repo}/contents/{path}"
        resp = self._get(url, headers=self._ghe_headers(), params={"ref": branch})
        self._check(
            resp,
            what=f"{parsed.owner}/{parsed.repo}@{branch}:{path}",
            private_hint="private repo? set ECHELON_GITHUB_TOKEN",
        )
        try:
            data = resp.json()
            b64 = data.get("content", "")
        except ValueError as exc:
            raise RemoteError(f"Unexpected GHE response for {path}") from exc
        if not b64:
            raise RemoteError(f"Empty content from GHE for {path}", status=404)
        try:
            return base64.b64decode(b64).decode("utf-8", errors="replace")
        except ValueError as exc:
            raise RemoteError(f"Cannot decode GHE content for {path}: {exc}") from exc

    def _ghe_commit(self, parsed: ParsedRemote) -> CommitInfo | None:
        url = f"https://{parsed.host}/api/v3/repos/{parsed.owner}/{parsed.repo}/commits"
        resp = self._get(
            url,
            headers=self._ghe_headers(),
            params={"path": parsed.file_path, "sha": parsed.branch, "per_page": 1},
        )
        if resp.status_code != 200:
            return None
        try:
            items = resp.json()
        except ValueError:
            return None
        if not items:
            return None
        item = items[0]
        commit = item.get("commit") or {}
        author = (commit.get("author") or {}).get("name", "")
        message = (
            str(commit.get("message", "")).splitlines()[0][:300] if commit.get("message") else ""
        )
        return CommitInfo(
            sha=str(item.get("sha", "")),
            url=str(item.get("html_url", "")),
            message=message,
            author=str(author),
            date="",
        )

    # ---------- GitLab (files API: slash-safe via query params) ----------

    def _gitlab_headers(self) -> dict:
        return {"PRIVATE-TOKEN": self.gitlab_token} if self.gitlab_token else {}

    def _gitlab_project(self, parsed: ParsedRemote) -> str:
        return quote(f"{parsed.owner}/{parsed.repo}", safe="")

    def _gitlab_file(self, branch: str, parsed: ParsedRemote, path: str) -> str:
        url = (
            f"https://{parsed.host}/api/v4/projects/{self._gitlab_project(parsed)}"
            f"/repository/files/{quote(path, safe='')}/raw"
        )
        resp = self._get(url, headers=self._gitlab_headers(), params={"ref": branch})
        self._check(
            resp,
            what=f"{parsed.owner}/{parsed.repo}@{branch}:{path}",
            private_hint="private project? set ECHELON_GITLAB_TOKEN",
        )
        return resp.text

    def _gitlab_commit(self, parsed: ParsedRemote) -> CommitInfo | None:
        url = (
            f"https://{parsed.host}/api/v4/projects/{self._gitlab_project(parsed)}"
            "/repository/commits"
        )
        resp = self._get(
            url,
            headers=self._gitlab_headers(),
            params={"path": parsed.file_path, "ref_name": parsed.branch, "per_page": 1},
        )
        if resp.status_code != 200:
            return None
        try:
            items = resp.json()
        except ValueError:
            return None
        if not items:
            return None
        item = items[0]
        sha = str(item.get("id", ""))
        web_url = str(
            item.get("web_url", "")
            or f"https://{parsed.host}/{parsed.owner}/{parsed.repo}/-/commit/{sha}"
        )
        return CommitInfo(
            sha=sha,
            url=web_url,
            message=str(item.get("title", ""))[:300],
            author=str(item.get("author_name", "")),
            date=str(item.get("created_at", "")),
        )

    # ---------- Gitea / Codeberg (contents API, base64) ----------

    def _gitea_headers(self) -> dict:
        return {"Authorization": f"token {self.gitea_token}"} if self.gitea_token else {}

    def _gitea_file(self, branch: str, parsed: ParsedRemote, path: str) -> str:
        url = f"https://{parsed.host}/api/v1/repos/{parsed.owner}/{parsed.repo}/contents/{path}"
        resp = self._get(url, headers=self._gitea_headers(), params={"ref": branch})
        self._check(
            resp,
            what=f"{parsed.owner}/{parsed.repo}@{branch}:{path}",
            private_hint="private repo? set ECHELON_GITEA_TOKEN",
        )
        try:
            b64 = resp.json().get("content", "")
        except ValueError as exc:
            raise RemoteError(f"Unexpected Gitea response for {path}") from exc
        if not b64:
            raise RemoteError(f"Empty content from Gitea for {path}", status=404)
        try:
            return base64.b64decode(b64).decode("utf-8", errors="replace")
        except ValueError as exc:
            raise RemoteError(f"Cannot decode Gitea content for {path}: {exc}") from exc

    def _gitea_commit(self, parsed: ParsedRemote) -> CommitInfo | None:
        url = f"https://{parsed.host}/api/v1/repos/{parsed.owner}/{parsed.repo}/commits"
        resp = self._get(
            url,
            headers=self._gitea_headers(),
            params={"sha": parsed.branch, "path": parsed.file_path, "limit": 1},
        )
        if resp.status_code != 200:
            return None
        try:
            items = resp.json()
        except ValueError:
            return None
        if not items:
            return None
        item = items[0]
        sha = str(item.get("sha", ""))
        html_url = str(
            item.get("html_url", "")
            or f"https://{parsed.host}/{parsed.owner}/{parsed.repo}/commit/{sha}"
        )
        commit = item.get("commit") or {}
        message = (
            str(commit.get("message", "")).splitlines()[0][:300] if commit.get("message") else ""
        )
        author = (
            ((item.get("author") or {}).get("login", ""))
            if isinstance(item.get("author"), dict)
            else ""
        )
        return CommitInfo(sha=sha, url=html_url, message=message, author=str(author), date="")

    # ---------- Bitbucket Cloud ----------

    def _bitbucket_auth(self) -> tuple | None:
        if self.bb_user and self.bb_app_password:
            return (self.bb_user, self.bb_app_password)
        return None

    def _bitbucket_api_base(self, host: str) -> str:
        # Bitbucket Cloud serves the API from api.bitbucket.org; any other
        # host is attempted as-is (clear 404 if it is not API-compatible).
        api_host = "api.bitbucket.org" if host == "bitbucket.org" else host
        return f"https://{api_host}/2.0/repositories"

    def _bitbucket_file(self, branch: str, parsed: ParsedRemote, path: str) -> str:
        url = f"{self._bitbucket_api_base(parsed.host)}/{parsed.owner}/{parsed.repo}/src/{branch}/{path}"
        resp = self._get(url, auth=self._bitbucket_auth())
        self._check(
            resp,
            what=f"{parsed.owner}/{parsed.repo}@{branch}:{path}",
            private_hint="private repo? set ECHELON_BITBUCKET_USER + ECHELON_BITBUCKET_APP_PASSWORD",
        )
        return resp.text

    def _bitbucket_commit(self, parsed: ParsedRemote) -> CommitInfo | None:
        url = (
            f"{self._bitbucket_api_base(parsed.host)}/{parsed.owner}/{parsed.repo}"
            f"/commits/{parsed.branch}"
        )
        resp = self._get(
            url, auth=self._bitbucket_auth(), params={"path": parsed.file_path, "pagelen": 1}
        )
        if resp.status_code != 200:
            return None
        try:
            values = resp.json().get("values", [])
        except ValueError:
            return None
        if not values:
            return None
        item = values[0]
        sha = str(item.get("hash", ""))
        links = item.get("links") or {}
        html = links.get("html") or {}
        commit_url = str(
            html.get("href", "")
            or f"https://{parsed.host}/{parsed.owner}/{parsed.repo}/commits/{sha}"
        )
        raw_author = str((item.get("author") or {}).get("raw", ""))
        author = raw_author.split("<")[0].strip()
        message = str(item.get("message", "")).splitlines()[0][:300] if item.get("message") else ""
        return CommitInfo(
            sha=sha, url=commit_url, message=message, author=author, date=str(item.get("date", ""))
        )

    # ---------- SourceHut (public blob endpoint) ----------

    def _sourcehut_file(self, branch: str, parsed: ParsedRemote, path: str) -> str:
        url = f"https://{parsed.host}/{parsed.owner}/{parsed.repo}/blob/{branch}/{path}"
        resp = self._get(url)
        self._check(resp, what=f"{parsed.owner}/{parsed.repo}@{branch}:{path}")
        return resp.text

    # ---------- raw text ----------

    def _raw_text_file(self, parsed: ParsedRemote) -> str:
        url = parsed.file_url.split("#", 1)[0]
        resp = self._get(url)
        self._check(resp, what=url[:120])
        ctype = resp.headers.get("Content-Type", "")
        if "html" in ctype.lower():
            print(f"Warning: {url[:80]} returned HTML; raw provider expects plain text.")
        return resp.text
