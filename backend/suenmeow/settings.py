from dataclasses import dataclass, field
from pathlib import Path
import os


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    key_file: Path
    origin: str
    secure_cookie: bool
    session_hours: int = 12

    @classmethod
    def env(cls):
        origin = os.getenv("PUBLIC_ORIGIN", "http://localhost:8000").rstrip("/")
        return cls(
            os.getenv("DATABASE_URL", "sqlite:///runtime/development.sqlite3"),
            Path(os.getenv("ENCRYPTION_KEY_FILE", "secrets/encryption.key")),
            origin,
            origin.startswith("https://"),
        )
