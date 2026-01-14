from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional


@dataclass
class SnippetConfig:
    id: str
    owner: str
    repo: str
    branch: str
    file_path: str
    start_line: int
    end_line: int
    file_url: str
    note: str = ""
    original_code: str = ""
    last_seen_code: str = ""


@dataclass
class AppConfig:
    webhook_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    interval_seconds: int = 300
    ollama_endpoint: str = ""
    ollama_model: str = ""
    gemini_api_key: str = ""
    gemini_model: str = ""
    openai_key: str = ""
    openai_model: str = ""
    snippets: List[SnippetConfig] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "webhook_url": self.webhook_url,
            "telegram_bot_token": self.telegram_bot_token,
            "telegram_chat_id": self.telegram_chat_id,
            "interval_seconds": self.interval_seconds,
            "ollama_endpoint": self.ollama_endpoint,
            "ollama_model": self.ollama_model,
            "gemini_api_key": self.gemini_api_key,
            "gemini_model": self.gemini_model,
            "openai_key": self.openai_key,
            "openai_model": self.openai_model,
            "snippets": [asdict(s) for s in (self.snippets or [])],
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "AppConfig":
        snippets_data = data.get("snippets", [])
        snippets: List[SnippetConfig] = []
        for s in snippets_data:
            snippets.append(
                SnippetConfig(
                    id=s["id"],
                    owner=s["owner"],
                    repo=s["repo"],
                    branch=s["branch"],
                    file_path=s["file_path"],
                    start_line=s["start_line"],
                    end_line=s["end_line"],
                    file_url=s["file_url"],
                    note=s.get("note", ""),
                    original_code=s.get("original_code", ""),
                    last_seen_code=s.get("last_seen_code", ""),
                )
            )
        return AppConfig(
            webhook_url=data.get("webhook_url", ""),
            telegram_bot_token=data.get("telegram_bot_token", ""),
            telegram_chat_id=str(data.get("telegram_chat_id", "") or ""),
            interval_seconds=data.get("interval_seconds", 300),
            ollama_endpoint=data.get("ollama_endpoint", ""),
            ollama_model=data.get("ollama_model", ""),
            gemini_api_key=data.get("gemini_api_key", ""),
            gemini_model=data.get("gemini_model", ""),
            openai_key=data.get("openai_key", ""),
            openai_model=data.get("openai_model", ""),
            snippets=snippets,
        )

    def find_snippet_by_url(self, url: str) -> Optional[SnippetConfig]:
        for s in self.snippets or []:
            if s.file_url == url:
                return s
        return None

    def remove_snippet_by_url(self, url: str) -> bool:
        if not self.snippets:
            return False
        original_len = len(self.snippets)
        self.snippets = [s for s in self.snippets if s.file_url != url]
        return len(self.snippets) != original_len
