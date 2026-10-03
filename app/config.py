"""Settings read from environment variables. Everything has a default so the demo starts with none."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    db_path: str = "data/app.db"
    anthropic_api_key: str | None = None
    model: str = "claude-haiku-4-5"
    monthly_limit: int = 5  # free plan: proposals that can be written per calendar month
    session_days: int = 7
    cookie_secure: bool = False  # set COOKIE_SECURE=1 when the site is served over HTTPS
    max_login_failures: int = 5
    login_window: int = 900  # seconds

    @classmethod
    def from_env(cls) -> "Settings":
        d = cls()
        return cls(
            db_path=os.environ.get("APP_DB", d.db_path),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
            model=os.environ.get("PROPOSAL_MODEL", d.model),
            monthly_limit=int(os.environ.get("MONTHLY_LIMIT", d.monthly_limit)),
            session_days=int(os.environ.get("SESSION_DAYS", d.session_days)),
            cookie_secure=os.environ.get("COOKIE_SECURE", "0") == "1",
        )
