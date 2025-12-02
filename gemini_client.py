import requests
from typing import Optional


class GeminiClient:
    def __init__(self, api_key: str, model: str, endpoint: Optional[str] = None):
        self.api_key = api_key
        self.model = model
        self.base_endpoint = (endpoint or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")

    def summarize_diff(self, diff_text: str) -> Optional[str]:
        if not diff_text.strip():
            return None
        url = f"{self.base_endpoint}/models/{self.model}:generateContent"
        system_instruction = (
            "You are a code review assistant. The user will send you a unified diff for a SMALL CODE SNIPPET, "
            "not the whole file. The diff only includes changed lines (and sometimes @@ hunk headers). "
            "Summarize the change in 1–3 short bullet points, focusing on behavior changes, security impact, "
            "and configuration changes. Reply in plain text, no markdown code fences."
        )
        user_text = f"Here is the diff:\n\n{diff_text}"
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": system_instruction},
                        {"text": "\n\n"},
                        {"text": user_text},
                    ],
                }
            ]
        }
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            candidates = data.get("candidates") or []
            if not candidates:
                return None
            content = candidates[0].get("content") or {}
            parts = content.get("parts") or []
            texts = [p.get("text", "") for p in parts if isinstance(p, dict)]
            summary = "\n".join(t for t in texts if t).strip()
            return summary or None
        except Exception as e:
            print(f"Error talking to Gemini at {url}: {e}")
            return None
