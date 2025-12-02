import requests
from typing import Optional


class OllamaClient:
    def __init__(self, endpoint: str, model: str):
        self.endpoint = endpoint.rstrip("/")
        self.model = model

    def summarize_diff(self, diff_text: str) -> Optional[str]:
        if not diff_text.strip():
            return None
        url = f"{self.endpoint}/api/chat"
        system_prompt = (
            "You are a code review assistant. The user will send a unified diff containing only changed lines. "
            "Summarize the change in 1–3 short bullet points, focusing on behavior changes, security impact, and configuration changes. Reply in plain text."
        )
        user_prompt = f"Diff:\n\n{diff_text}"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
        }
        try:
            resp = requests.post(url, json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            message = data.get("message") or {}
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
            return str(data)[:1000]
        except Exception as e:
            print(f"Error talking to Ollama at {url}: {e}")
            return None
