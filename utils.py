import hashlib

from github_client import ParsedGitHubURL


def snippet_id_from_parsed(parsed: ParsedGitHubURL) -> str:
    base = f"{parsed.owner}/{parsed.repo}/{parsed.branch}/{parsed.file_path}#L{parsed.start_line}-L{parsed.end_line}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]
