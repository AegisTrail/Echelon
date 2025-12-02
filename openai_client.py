import requests
from typing import Optional


class OpenAIClient:
    def __init__(self, api_key: str, model: str, endpoint: Optional[str] = None):
        self.api_key = api_key
        self.model = model
        self.base_endpoint = (endpoint or "https://api.openai.com/v1").rstrip("/")

    def summarize_diff(self, diff_text: str) -> Optional[str]:
        if not diff_text.strip():
            return None
        url = f"{self.base_endpoint}/chat/completions"
        system_prompt = (
            "You are a code review assistant. The user will send a unified diff that contains only changed lines. "
            "Summarize the change in 1–3 concise bullet points, focusing on behavior changes, security impact, and configuration changes. Reply in plain text."
        )
        user_prompt = f"Diff:\n\n{diff_text}"

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 256,
            "temperature": 0.0,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            choices = data.get("choices") or []
            if choices:
                msg = choices[0].get("message") or {}
                content = msg.get("content")
                if isinstance(content, str):
                    return content.strip()
            return str(data)[:1000]
        except Exception as e:
            print(f"Error talking to OpenAI at {url}: {e}")
            return None
