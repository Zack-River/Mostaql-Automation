"""
db/database.py — Async SQLite layer using aiosqlite.

Responsibilities:
  - Track seen job IDs to prevent duplicate notifications.
  - Store basic stats (total notified, last checked time).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import aiosqlite

logger = logging.getLogger(__name__)


class Database:
    """
    Lightweight async SQLite wrapper.
    One connection is kept open for the lifetime of the bot.
    """

    def __init__(self, db_path: str) -> None:
        self._path = db_path
        self._conn: aiosqlite.Connection | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────────────
    async def connect(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._init_schema()
        logger.info(f"Database connected: {self._path}")

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            logger.info("Database connection closed.")

    async def _init_schema(self) -> None:
        assert self._conn
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS seen_jobs (
                job_id      TEXT PRIMARY KEY,
                title       TEXT,
                url         TEXT,
                notified_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bot_stats (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            INSERT OR IGNORE INTO bot_stats (key, value)
            VALUES
                ('total_notified', '0'),
                ('last_checked',   '');
            """
        )
        await self._conn.commit()

    # ── Job Deduplication ──────────────────────────────────────────────────────
    async def is_new_job(self, job_id: str) -> bool:
        """Return True if this job_id has NOT been seen before."""
        assert self._conn
        async with self._conn.execute(
            "SELECT 1 FROM seen_jobs WHERE job_id = ?", (job_id,)
        ) as cursor:
            return await cursor.fetchone() is None

    async def mark_job_seen(self, job_id: str, title: str = "", url: str = "") -> None:
        """Record a job as seen (called after notification is sent)."""
        assert self._conn
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            """
            INSERT OR IGNORE INTO seen_jobs (job_id, title, url, notified_at)
            VALUES (?, ?, ?, ?)
            """,
            (job_id, title, url, now),
        )
        # Increment total counter
        await self._conn.execute(
            "UPDATE bot_stats SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT) "
            "WHERE key = 'total_notified'"
        )
        await self._conn.commit()

    async def get_job_url(self, job_id: str) -> str | None:
        """Fetch the URL of a previously seen job."""
        assert self._conn
        async with self._conn.execute(
            "SELECT url FROM seen_jobs WHERE job_id = ?", (job_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row["url"] if row else None

    # ── Stats ──────────────────────────────────────────────────────────────────
    async def update_last_checked(self) -> None:
        assert self._conn
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            "UPDATE bot_stats SET value = ? WHERE key = 'last_checked'", (now,)
        )
        await self._conn.commit()

    async def get_stats(self) -> dict[str, str]:
        assert self._conn
        async with self._conn.execute(
            "SELECT key, value FROM bot_stats"
        ) as cursor:
            rows = await cursor.fetchall()
        stats = {row["key"]: row["value"] for row in rows}

        # Also get total seen jobs count
        async with self._conn.execute(
            "SELECT COUNT(*) as cnt FROM seen_jobs"
        ) as cursor:
            row = await cursor.fetchone()
            stats["total_seen"] = str(row["cnt"]) if row else "0"

        return stats
