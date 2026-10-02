"""Multi-provider URL parsing: GitHub (+Enterprise), GitLab, Gitea, Bitbucket, SourceHut, raw text.

Every supported shape carries an explicit branch and a line fragment, e.g.::

    GitHub:    https://github.com/o/r/blob/main/f.py#L26-L31
    GitLab:    https://gitlab.com/g/sub/r/-/blob/main/f.py#L26-31
    Gitea:     https://codeberg.org/o/r/src/branch/main/f.py#L26-L31
    Bitbucket: https://bitbucket.org/o/r/src/main/f.py#lines-26:31
    SourceHut: https://git.sr.ht/~u/r/tree/main/item/f.py#L26-31
    Raw text:  https://example.com/f.py#L10-L20   (body fetched as plain text)

Branches containing slashes are resolved at ``--add`` time by probing
candidates longest-first (see :mod:`remote_client`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NoReturn
from urllib.parse import urlparse

PROVIDERS = ("github", "gitlab", "gitea", "bitbucket", "sourcehut", "raw")

_GITHUB_HOSTS = {"github.com", "www.github.com"}
_MAX_SNIPPET_LINES = 500
_MAX_BRANCH_SEGMENTS = 8  # bounds add-time probing

_fragment_re = re.compile(r"L(\d+)(?:-L?(\d+))?")
_bb_fragment_re = re.compile(r"lines-(\d+)(?::(\d+))?")
_namespace_re = re.compile(r"^[A-Za-z0-9_.\-~/]+$")
_repo_re = re.compile(r"^[A-Za-z0-9_.\-]+$")

SUPPORTED_HELP = (
    "Supported URL shapes (all need a #line fragment):\n"
    "  GitHub:    https://github.com/<owner>/<repo>/blob/<branch>/<path>#L1-L5\n"
    "  GitLab:    https://<host>/<group>/.../<repo>/-/blob/<branch>/<path>#L1-5\n"
    "  Gitea:     https://<host>/<owner>/<repo>/src/branch/<branch>/<path>#L1-L5\n"
    "  Bitbucket: https://bitbucket.org/<owner>/<repo>/src/<branch>/<path>#lines-1:5\n"
    "  SourceHut: https://<host>/~<user>/<repo>/tree/<branch>/item/<path>#L1-5\n"
    "  Raw text:  https://<host>/<path>#L1-L5  (plain-text body)"
)


@dataclass
class ParsedRemote:
    provider: str
    host: str
    owner: str  # namespace; may contain "/" for GitLab subgroups, "~u" for SourceHut
    repo: str
    branch: str  # "" for raw URLs
    file_path: str
    start_line: int
    end_line: int
    file_url: str
    remainder: str = ""  # full branch/path remainder for add-time probing
    fixed_ref: bool = False  # True: branch is exact, skip probing


def _fail(url: str, why: str) -> NoReturn:
    from models import ConfigError  # deferred: models must not import providers

    raise ConfigError(f"{why}: {url!r}\n{SUPPORTED_HELP}")


def _check_range(start: int, end: int, url: str) -> tuple[int, int]:
    if start < 1 or end < start:
        _fail(url, "Invalid line range")
    if end - start + 1 > _MAX_SNIPPET_LINES:
        _fail(url, f"Line range too large (max {_MAX_SNIPPET_LINES} lines)")
    return start, end


def _parse_lines_fragment(fragment: str, url: str) -> tuple[int, int]:
    match = _fragment_re.fullmatch(fragment)
    if not match:
        _fail(url, "URL must include a line fragment like #L26-L31")
    start = int(match.group(1))
    end = int(match.group(2) or match.group(1))
    return _check_range(start, end, url)


def _check_namespace(value: str, url: str) -> str:
    if not value or not _namespace_re.match(value):
        _fail(url, f"Invalid namespace {value!r}")
    if ".." in value.split("/"):
        _fail(url, f"Invalid namespace {value!r}")
    return value


def _check_repo(value: str, url: str) -> str:
    if not value or not _repo_re.match(value):
        _fail(url, f"Invalid repo {value!r}")
    return value


def _check_file_path(value: str, url: str) -> str:
    if not value or len(value) > 500:
        _fail(url, f"Invalid file path {value!r}")
    if ".." in value.split("/") or value.startswith(".git/"):
        _fail(url, f"Invalid file path {value!r}")
    return value


def parse_any_url(url: str) -> ParsedRemote:
    """Parse any supported code URL into a :class:`ParsedRemote`."""
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        _fail(url, "URL must be https://")
    host = parsed.hostname.lower()
    path = parsed.path.strip("/")
    fragment = parsed.fragment

    # --- GitLab: /<namespace>/<repo>/-/blob/<branch>/<path> ---
    if "/-/blob/" in f"/{path}/":
        ns_repo, _, remainder = path.partition("/-/blob/")
        parts = ns_repo.split("/")
        if len(parts) >= 2 and remainder:
            namespace = "/".join(parts[:-1])
            repo = parts[-1]
            branch, _, file_path = remainder.partition("/")
            if file_path and len(remainder.split("/")) <= _MAX_BRANCH_SEGMENTS + 1:
                start, end = _parse_lines_fragment(fragment, url)
                return ParsedRemote(
                    provider="gitlab",
                    host=host,
                    owner=_check_namespace(namespace, url),
                    repo=_check_repo(repo, url),
                    branch=branch,
                    file_path=_check_file_path(file_path, url),
                    start_line=start,
                    end_line=end,
                    file_url=url,
                    remainder=remainder,
                )
        _fail(url, "Invalid GitLab file URL")

    segments = path.split("/")

    # --- GitHub shape: /<owner>/<repo>/blob/<branch>/<path> (also GHE hosts) ---
    if len(segments) >= 4 and segments[2] == "blob":
        owner, repo = segments[0], segments[1]
        remainder = "/".join(segments[3:])
        branch, _, file_path = remainder.partition("/")
        if remainder and file_path:
            start, end = _parse_lines_fragment(fragment, url)
            return ParsedRemote(
                provider="github",
                host=host,
                owner=_check_namespace(owner, url),
                repo=_check_repo(repo, url),
                branch=branch,
                file_path=_check_file_path(file_path, url),
                start_line=start,
                end_line=end,
                file_url=url,
                remainder=remainder,
            )
        _fail(url, "Invalid GitHub file URL")

    # --- Gitea: /<owner>/<repo>/src/<branch|commit|tag>/<ref>/<path> ---
    # NOTE: requires >= 6 segments so a Bitbucket branch literally named
    # "branch"/"tag"/"commit" still falls through to the Bitbucket rule.
    if len(segments) >= 6 and segments[2] == "src" and segments[3] in ("branch", "commit", "tag"):
        owner, repo, _, kind, ref = segments[:5]
        file_path = "/".join(segments[5:])
        if file_path:
            start, end = _parse_lines_fragment(fragment, url)
            fixed = kind in ("commit", "tag")
            # Remainder excludes the kind marker so probing splits branch/path only.
            remainder = f"{ref}/{file_path}" if not fixed else ""
            return ParsedRemote(
                provider="gitea",
                host=host,
                owner=_check_namespace(owner, url),
                repo=_check_repo(repo, url),
                branch=ref,
                file_path=_check_file_path(file_path, url),
                start_line=start,
                end_line=end,
                file_url=url,
                remainder=remainder,
                fixed_ref=fixed,
            )
        _fail(url, "Invalid Gitea file URL")

    # --- SourceHut: /~<user>/<repo>/tree/<branch>/item/<path> ---
    if "/tree/" in path and "/item/" in path and len(segments) >= 5:
        tree_idx = segments.index("tree")
        if tree_idx >= 2 and "item" in segments[tree_idx + 1 :]:
            item_idx = segments.index("item", tree_idx + 1)
            namespace = "/".join(segments[: tree_idx - 1]) or ""
            repo = segments[tree_idx - 1]
            branch = "/".join(segments[tree_idx + 1 : item_idx])
            file_path = "/".join(segments[item_idx + 1 :])
            if namespace and repo and branch and file_path:
                start, end = _parse_lines_fragment(fragment, url)
                return ParsedRemote(
                    provider="sourcehut",
                    host=host,
                    owner=_check_namespace(namespace, url),
                    repo=_check_repo(repo, url),
                    branch=branch,
                    file_path=_check_file_path(file_path, url),
                    start_line=start,
                    end_line=end,
                    file_url=url,
                    fixed_ref=True,
                )
        _fail(url, "Invalid SourceHut file URL")

    # --- Bitbucket: /<owner>/<repo>/src/<branch>/<path> ---
    if len(segments) >= 4 and segments[2] == "src":
        owner, repo = segments[0], segments[1]
        remainder = "/".join(segments[3:])
        branch, _, file_path = remainder.partition("/")
        if remainder and file_path:
            bb = _bb_fragment_re.fullmatch(fragment)
            if bb:
                start, end = _check_range(int(bb.group(1)), int(bb.group(2) or bb.group(1)), url)
            else:
                start, end = _parse_lines_fragment(fragment, url)
            return ParsedRemote(
                provider="bitbucket",
                host=host,
                owner=_check_namespace(owner, url),
                repo=_check_repo(repo, url),
                branch=branch,
                file_path=_check_file_path(file_path, url),
                start_line=start,
                end_line=end,
                file_url=url,
                remainder=remainder,
            )
        _fail(url, "Invalid Bitbucket file URL")

    # --- Raw text fallback: any https URL with a #Lx-Ly fragment ---
    # A bare /tree/ URL without /item/ is a directory listing, not a file:
    # fail loudly instead of fetching HTML as "raw text".
    if "/tree/" in path and "/item/" not in path:
        _fail(url, "SourceHut file URLs need /tree/<branch>/item/<path>")
    if fragment and _fragment_re.fullmatch(fragment):
        start, end = _parse_lines_fragment(fragment, url)
        return ParsedRemote(
            provider="raw",
            host=host,
            owner=host,
            repo="raw",
            branch="",
            file_path=parsed.path or "/",
            start_line=start,
            end_line=end,
            file_url=url,
            fixed_ref=True,
        )

    _fail(url, "Unsupported URL (unknown provider and no #Lx-Ly fragment)")


def is_github_cloud(parsed: ParsedRemote) -> bool:
    return parsed.provider == "github" and parsed.host in _GITHUB_HOSTS
