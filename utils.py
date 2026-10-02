import hashlib

from github_client import ParsedGitHubURL
from providers import ParsedRemote


def snippet_id_from_parsed(parsed: ParsedGitHubURL | ParsedRemote) -> str:
    provider = getattr(parsed, "provider", "github") or "github"
    host = getattr(parsed, "host", "") or ""
    base = (
        f"{provider}/{host}/{parsed.owner}/{parsed.repo}/{parsed.branch}/"
        f"{parsed.file_path}#L{parsed.start_line}-L{parsed.end_line}"
    )
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]
