import json
import os
from typing import Optional

from models import AppConfig


class ConfigManager:
    def __init__(self, path: str = "config.json"):
        self.path = path

    def load(self) -> AppConfig:
        if not os.path.exists(self.path):
            return AppConfig(snippets=[])
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return AppConfig.from_dict(data)

    def save(self, config: AppConfig) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(config.to_dict(), f, indent=2)
