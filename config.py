"""
config.py — centralised settings loaded from .env
All other modules import from here; no direct os.getenv calls elsewhere.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load the .env file from the project root (one level up from this file)
load_dotenv(dotenv_path=Path(__file__).parent / ".env")


def _require(key: str) -> str:
    """Return env var value or raise a clear error at startup."""
    val = os.getenv(key)
    if not val:
        raise EnvironmentError(
            f"Missing required environment variable: {key}\n"
            f"Please copy .env.example → .env and fill in your values."
        )
    return val


def _optional(key: str, default: str = "") -> str:
    return os.getenv(key, default)


@dataclass(frozen=True)
class Settings:
    # ── Telegram ──────────────────────────────────────────────────────
    telegram_bot_token: str
    telegram_chat_id: str

    # ── Mostaql ───────────────────────────────────────────────────────
    mostaql_email: str
    mostaql_password: str
    mostaql_category_id: int

    # ── Mostaql Session Cookies ───────────────────────────────────────
    mostaql_cookie_awsalb: str
    mostaql_cookie_awsalbcors: str
    mostaql_cookie_session: str
    mostaql_cookie_xsrf: str

    # ── Gemini AI ─────────────────────────────────────────────────────
    gemini_api_key: str
    gemini_fallback_api_key: str
    gemini_model: str

    # ── Bot Behaviour ─────────────────────────────────────────────────
    poll_interval_seconds: int
    base_proposal_prompt: str

    # ── Derived ───────────────────────────────────────────────────────
    mostaql_jobs_url: str = field(init=False)
    db_path: str = field(init=False)

    def __post_init__(self) -> None:
        # Use object.__setattr__ because the dataclass is frozen
        object.__setattr__(
            self,
            "mostaql_jobs_url",
            "https://mostaql.com/projects/development?sort=date",
        )
        db_dir = Path(__file__).parent / "data"
        db_dir.mkdir(exist_ok=True)
        object.__setattr__(self, "db_path", str(db_dir / "mostaql_bot.db"))

    @property
    def is_authenticated(self) -> bool:
        """True when Mostaql credentials are configured."""
        return bool(self.mostaql_email and self.mostaql_password)

    @property
    def has_session_cookies(self) -> bool:
        """True when browser session cookies are available."""
        return bool(self.mostaql_cookie_session)


def load_settings() -> Settings:
    """
    Build and return the global Settings object.
    Call once from main.py and pass the result around.
    """
    return Settings(
        telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_require("TELEGRAM_CHAT_ID"),
        mostaql_email=_optional("MOSTAQL_EMAIL"),
        mostaql_password=_optional("MOSTAQL_PASSWORD"),
        mostaql_category_id=int(_optional("MOSTAQL_CATEGORY_ID", "1")),
        mostaql_cookie_awsalb=_optional("MOSTAQL_COOKIE_AWSALB"),
        mostaql_cookie_awsalbcors=_optional("MOSTAQL_COOKIE_AWSALBCORS"),
        mostaql_cookie_session=_optional("MOSTAQL_COOKIE_SESSION"),
        mostaql_cookie_xsrf=_optional("MOSTAQL_COOKIE_XSRF"),
        gemini_api_key=_optional("GEMINI_API_KEY"),
        gemini_fallback_api_key=_optional("GEMINI_FALLBACK_API_KEY", ""),
        gemini_model=_optional("GEMINI_MODEL", "gemini-3.5-flash"),
        poll_interval_seconds=int(_optional("POLL_INTERVAL_SECONDS", "30")),
        base_proposal_prompt=_optional(
            "BASE_PROPOSAL_PROMPT",
            "أنا مطور محترف ذو خبرة واسعة."
        ),
    )
