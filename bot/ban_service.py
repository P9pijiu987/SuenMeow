from __future__ import annotations

import re


class BanService:
    def __init__(self, bot_username: str) -> None:
        self.pattern = re.compile(rf"/ban\s+@?{re.escape(bot_username)}\b", re.IGNORECASE)

    def contains_ban_command(self, text: str) -> bool:
        return bool(self.pattern.search(text))
