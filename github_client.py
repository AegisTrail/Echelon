import re
from dataclasses import dataclass
from typing import Tuple
from urllib.parse import urlparse

import requests


@dataclass
class ParsedGitHubURL:
    owner: str
    repo: str
    branch: str
    file_path: str
    start_line: int
    end_line: int
    file_url: str


class GitHubClient:
    GITHUB_RAW_BASE = "https://raw.githubusercontent.com"

    def parse_github_url(self, url: str) -> ParsedGitHubURL:
        parsed = urlparse(url)
        path_parts = parsed.path.strip("/").split("/")

        if len(path_parts) < 2:
            raise ValueError(f"Invalid GitHub URL: {url}")

        owner = path_parts[0]
        repo = path_parts[1]

        branch = "main"
        file_parts_start_index = 2

        if len(path_parts) >= 4 and path_parts[2] == "blob":
            branch = path_parts[3]
            file_parts_start_index = 4

        file_path = "/".join(path_parts[file_parts_start_index:])
        if not file_path:
            raise ValueError(f"Could not determine file path from URL: {url}")

        fragment = parsed.fragment
        if not fragment:
            raise ValueError("URL must include line fragment, e.g. #L26-L31")

        match = re.match(r"L(\d+)(?:-L?(\d+))?", fragment)
        if not match:
            raise ValueError(f"Invalid line fragment in URL: {fragment}")

        start_line = int(match.group(1))
        end_line = int(match.group(2) or match.group(1))

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

    def fetch_file_content(self, parsed: ParsedGitHubURL) -> str:
        raw_url = self.build_raw_url(parsed)
        resp = requests.get(raw_url, timeout=10)
        resp.raise_for_status()
        return resp.text

    def extract_lines(self, content: str, start_line: int, end_line: int) -> str:
        lines = content.splitlines()
        if start_line < 1 or end_line > len(lines):
            raise ValueError("Requested lines are out of range of the file")
        snippet_lines = lines[start_line - 1 : end_line]
        return "\n".join(snippet_lines)
