"""
auth/session_manager.py — Central manager for Mostaql session cookies.

Responsibilities:
  - Load cookies from cookies.json (priority) or .env fallback
  - Save updated cookies to cookies.json after /refresh_session
  - Provide update_all() to push new cookies to all live components
    (scraper + applicator) without requiring a restart
  - Detect expired sessions from HTTP responses
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scraper.mostaql import MostaqlScraper
    from bot.applicator import MostaqlApplicator

logger = logging.getLogger(__name__)

COOKIES_FILE = Path("cookies.json")

# Cookies we care about for Mostaql auth
SESSION_COOKIE_NAMES = ["AWSALB", "AWSALBCORS", "mostaqlweb", "XSRF-TOKEN"]


class SessionManager:
    """
    Single source of truth for Mostaql session cookies.
    Shared across scraper and applicator.
    """

    def __init__(self, env_cookies: dict[str, str] | None = None) -> None:
        self._cookies: dict[str, str] = {}
        # Priority: cookies.json > .env
        if COOKIES_FILE.exists():
            self._load_from_file()
        elif env_cookies:
            self._cookies = {k: v for k, v in env_cookies.items() if v}
            logger.info(f"Session cookies loaded from .env ({len(self._cookies)} cookies)")
        else:
            logger.warning("No session cookies found. Use /update_cookie to set them.")

        # Live references — populated by main.py after init
        self._scraper: "MostaqlScraper | None" = None
        self._applicator: "MostaqlApplicator | None" = None

    # ── Registration ──────────────────────────────────────────────────────────

    def register(
        self,
        scraper: "MostaqlScraper | None" = None,
        applicator: "MostaqlApplicator | None" = None,
    ) -> None:
        """Register live component references for push-updates."""
        if scraper:
            self._scraper = scraper
        if applicator:
            self._applicator = applicator

    # ── Cookie access ─────────────────────────────────────────────────────────

    @property
    def cookies(self) -> dict[str, str]:
        return dict(self._cookies)

    @property
    def has_cookies(self) -> bool:
        return bool(self._cookies.get("mostaqlweb"))

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load_from_file(self) -> None:
        try:
            with open(COOKIES_FILE, encoding="utf-8") as f:
                data = json.load(f)
            self._cookies = {k: v for k, v in data.items() if v}
            logger.info(f"Session cookies loaded from {COOKIES_FILE} ({len(self._cookies)} cookies)")
        except Exception as exc:
            logger.error(f"Failed to load {COOKIES_FILE}: {exc}")

    def _save_to_file(self) -> None:
        try:
            with open(COOKIES_FILE, "w", encoding="utf-8") as f:
                json.dump(self._cookies, f, ensure_ascii=False, indent=2)
            logger.info(f"Session cookies saved to {COOKIES_FILE}")
        except Exception as exc:
            logger.error(f"Failed to save {COOKIES_FILE}: {exc}")

    # ── Cookie extraction ─────────────────────────────────────────────────────

    def extract_from_client(self, client) -> dict[str, str]:
        """
        Pull session cookies from an httpx AsyncClient's cookie jar.
        Used after a successful login to capture fresh cookies.
        """
        extracted: dict[str, str] = {}
        for name in SESSION_COOKIE_NAMES:
            # httpx stores cookies in a Cookies object
            val = client.cookies.get(name)
            if not val:
                # Fallback: iterate the internal jar
                for cookie in client.cookies.jar:
                    if cookie.name == name:
                        val = cookie.value
                        break
            if val:
                extracted[name] = val
        return extracted

    # ── Update & push ─────────────────────────────────────────────────────────

    def update_and_push(self, new_cookies: dict[str, str]) -> None:
        """
        Save new cookies to file and push them to all live components.
        Call this after a successful login/2FA.
        """
        self._cookies.update({k: v for k, v in new_cookies.items() if v})
        self._save_to_file()

        # Push to scraper
        if self._scraper:
            self._scraper._inject_cookies(self._cookies)
            logger.info("Cookies pushed to scraper")

        # Push to applicator
        if self._applicator:
            self._applicator.update_cookies(self._cookies)
            logger.info("Cookies pushed to applicator")

        logger.info("All live components updated with fresh session cookies")

    # ── Expiry detection ──────────────────────────────────────────────────────

    @staticmethod
    def is_session_expired(response_text: str, status_code: int) -> bool:
        """Return True if an HTTP response indicates the session has expired."""
        if status_code in (401, 403):
            return True
        if "تسجيل الدخول" in response_text and "logout" not in response_text.lower():
            return True
        return False
