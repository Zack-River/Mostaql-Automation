"""
scheduler/job_monitor.py — Core orchestration: scrape → deduplicate → notify.

This module contains:
  1. `format_notification()` — builds the rich Telegram message text for a job
  2. `JobMonitor`           — owns the scraper/AI instances and runs the poll cycle
  3. Gemini rate-limit guard (free tier: 5 req/min → 12s minimum between calls)
"""
from __future__ import annotations

import asyncio
import html
import logging
import time

from aiogram import Bot

from ai.proposal import GeminiProposalGenerator
from bot import handlers as handler_cache
from bot.keyboards import job_keyboard
from config import Settings
from db.database import Database
from scraper.models import Job
from scraper.mostaql import MostaqlScraper

logger = logging.getLogger(__name__)

# Gemini free tier: 5 requests/min → enforce 13s between calls to stay safe
_GEMINI_MIN_INTERVAL = 13.0
_last_gemini_call: float = 0.0


# ── Notification Formatter ─────────────────────────────────────────────────────

def format_notification(job: Job) -> str:
    """
    Build a clean, well-structured Telegram HTML notification.

    Layout:
      🆕 Header
      📌 Title + link
      ─ divider ─
      📝 Description (clean, 350 chars max)
      ─ divider ─
      Project metadata block
      ─ divider ─
      Client profile block  (only if data exists)
      ─ divider ─
      🤖 AI Proposal        (only if generated)
    """
    lines: list[str] = []

    # ── Header ────────────────────────────────────────────────────────────────
    lines += [
        "🆕 <b>مشروع برمجي جديد على مستقل</b>",
        "",
        f"📌 <b><a href='{job.url}'>{job.title}</a></b>",
        "",
    ]

    # ── Description ───────────────────────────────────────────────────────────
    desc = (job.full_description or job.description_snippet).strip()
    # Collapse multiple newlines to a single space for cleaner Telegram display
    desc = " ".join(desc.split())
    if len(desc) > 350:
        desc = desc[:350] + "..."
    if desc:
        lines += [
            "📝 <b>الوصف:</b>",
            desc,
            "",
        ]

    # ── Project Metadata ──────────────────────────────────────────────────────
    lines.append("─── تفاصيل المشروع ───")
    if job.budget:
        lines.append(f"💰 <b>الميزانية:</b>  {job.budget}")
    if job.duration:
        lines.append(f"⏱ <b>مدة التنفيذ:</b>  {job.duration}")
    if job.status:
        lines.append(f"📊 <b>الحالة:</b>  {job.status}")
    if job.publish_date or job.time_posted:
        lines.append(f"📅 <b>النشر:</b>  {job.publish_date or job.time_posted}")
    if job.proposals_count:
        lines.append(f"📨 <b>العروض:</b>  {job.proposals_count}")
    if job.skills:
        lines.append(f"🏷 <b>المهارات:</b>  {job.skills_str}")
    lines.append("")

    # ── Client Profile ────────────────────────────────────────────────────────
    c = job.client
    has_client = any([c.name, c.hiring_rate, c.open_projects, c.in_progress_projects,
                      c.ongoing_communications, c.registration_date])
    if has_client:
        lines.append("─── صاحب المشروع ───")
        if c.name:
            lines.append(f"👤 <b>الاسم:</b>  {c.name}")
        if c.registration_date:
            lines.append(f"📆 <b>عضو منذ:</b>  {c.registration_date}")
        if c.hiring_rate:
            lines.append(f"{_rate_emoji(c.hiring_rate_label)} <b>معدل التوظيف:</b>  {c.hiring_rate}")
        if c.open_projects:
            lines.append(f"📂 <b>مشاريع مفتوحة:</b>  {c.open_projects}")
        if c.in_progress_projects:
            lines.append(f"🔨 <b>قيد التنفيذ:</b>  {c.in_progress_projects}")
        if c.ongoing_communications:
            lines.append(f"💬 <b>تواصل جارٍ:</b>  {c.ongoing_communications}")
        lines.append("")

    # ── Pre-application Questions ──────────────────────────────────────────────
    if job.has_questions:
        lines.append("─── أسئلة قبل التقديم ───")
        for i, q in enumerate(job.questions, 1):
            lines.append(f"❓ {i}. {q.question}")
        lines.append("")

    return "\n".join(lines)


def _rate_emoji(label: str) -> str:
    return {"excellent": "🌟", "good": "⭐", "average": "🔶", "bad": "🔴"}.get(label, "⭐")


# ── Job Monitor ────────────────────────────────────────────────────────────────

class JobMonitor:
    """
    Orchestrates the full scrape → deduplicate → generate → notify cycle.
    One instance lives for the lifetime of the bot, held by main.py.
    """

    def __init__(
        self,
        settings: Settings,
        bot: Bot,
        db: Database,
        scraper: MostaqlScraper,
        generator: GeminiProposalGenerator,
    ) -> None:
        self._settings = settings
        self._bot = bot
        self._db = db
        self._scraper = scraper
        self._generator = generator
        self._cycle_count = 0
        self._is_running = False
        self._first_run = True

    async def run_cycle(self) -> None:
        """
        Single monitor cycle — called every N seconds by APScheduler.
        """
        if self._is_running:
            logger.warning("Previous cycle is still running. Skipping this cycle.")
            return

        self._is_running = True
        self._cycle_count += 1
        logger.debug(f"Monitor cycle #{self._cycle_count} started.")

        try:
            jobs = await self._scraper.fetch_jobs_list()
            if not jobs:
                logger.warning("No jobs returned from listing page.")
                await self._db.update_last_checked()
                return

            new_jobs: list[Job] = []
            for job in jobs:
                if await self._db.is_new_job(job.id):
                    new_jobs.append(job)

            logger.info(
                f"Cycle #{self._cycle_count}: {len(jobs)} fetched, {len(new_jobs)} new."
            )

            # Silently seed the database on the first run to prevent spam
            if self._first_run:
                self._first_run = False
                if len(new_jobs) > 5:
                    logger.info("Initial startup detected. Seeding jobs silently without notifications.")
                    for job in new_jobs:
                        await self._db.mark_job_seen(job.id, title=job.title, url=job.url)
                    await self._db.update_last_checked()
                    return

            for job in new_jobs:
                try:
                    await self._process_new_job(job)
                except Exception as exc:
                    logger.error(f"Error processing job {job.id}: {exc}", exc_info=True)

            await self._db.update_last_checked()
        finally:
            self._is_running = False

    async def _process_new_job(self, job: Job) -> None:
        """Full pipeline for a single new job."""
        global _last_gemini_call

        # a. Fetch full details
        await self._scraper.fetch_job_details(job)

        # c. Send Telegram notification
        message_text = format_notification(job)
        keyboard = job_keyboard(job_url=job.url, job_id=job.id)

        await self._bot.send_message(
            chat_id=self._settings.telegram_chat_id,
            text=message_text,
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
        logger.info(f"✅ Notified: [{job.id}] {job.title[:60]}")

        # d. Mark as seen
        await self._db.mark_job_seen(job.id, title=job.title, url=job.url)
