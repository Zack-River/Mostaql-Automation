"""
main.py — Entry point for the Mostaql Telegram bot.

Startup sequence:
  1. Load settings from .env
  2. Connect to SQLite database
  3. Create MostaqlScraper + log in if credentials exist
  4. Create GeminiProposalGenerator
  5. Create aiogram Bot + Dispatcher
  6. Start APScheduler with the monitor cycle
  7. Start Telegram polling

Shutdown sequence (SIGINT / SIGTERM):
  1. Stop scheduler
  2. Stop polling
  3. Close scraper HTTP client
  4. Close database connection
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram import Bot, Dispatcher, BaseMiddleware
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web

from ai.proposal import GeminiProposalGenerator
from auth.session_manager import SessionManager
from bot import handlers
from bot.applicator import MostaqlApplicator
from config import load_settings
from db.database import Database
from scheduler.job_monitor import JobMonitor
from scraper.mostaql import MostaqlScraper

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


# ── Middleware ─────────────────────────────────────────────────────────────────
class AccessMiddleware(BaseMiddleware):
    def __init__(self, allowed_chat_id: str):
        self.allowed_chat_id = str(allowed_chat_id)

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user and str(user.id) != self.allowed_chat_id:
            logger.warning(f"Unauthorized access attempt from user ID: {user.id}")
            return
        return await handler(event, data)


async def main() -> None:
    # ── 1. Load configuration ──────────────────────────────────────────────────
    settings = load_settings()
    logger.info("Configuration loaded.")
    logger.info(f"  Poll interval : {settings.poll_interval_seconds}s")
    logger.info(f"  Category URL  : {settings.mostaql_jobs_url}")
    logger.info(f"  Authenticated : {settings.is_authenticated}")
    logger.info(f"  AI enabled    : {bool(settings.gemini_api_key)}")

    # ── 2. Database ────────────────────────────────────────────────────────────
    db = Database(settings.db_path)
    await db.connect()

    # ── 3. Session Manager (single source of truth for cookies) ────────────────
    env_cookies = None
    if settings.has_session_cookies:
        env_cookies = {
            "AWSALB": settings.mostaql_cookie_awsalb,
            "AWSALBCORS": settings.mostaql_cookie_awsalbcors,
            "mostaqlweb": settings.mostaql_cookie_session,
            "XSRF-TOKEN": settings.mostaql_cookie_xsrf,
        }

    session_manager = SessionManager(env_cookies=env_cookies)

    if session_manager.has_cookies:
        logger.info("  Auth mode     : SESSION COOKIES ✅")
    elif settings.is_authenticated:
        logger.info("  Auth mode     : EMAIL/PASSWORD (no cookies yet)")
    else:
        logger.info("  Auth mode     : PUBLIC (no auth)")

    # ── 4. Scraper ─────────────────────────────────────────────────────────────
    scraper = MostaqlScraper(
        jobs_url=settings.mostaql_jobs_url,
        mostaql_email=settings.mostaql_email,
        mostaql_password=settings.mostaql_password,
        session_cookies=session_manager.cookies if session_manager.has_cookies else None,
    )
    await scraper.__aenter__()

    # Only attempt login if no session cookies available
    if not session_manager.has_cookies and settings.is_authenticated:
        result = await scraper.login()
        if result is True:
            new_cookies = scraper.extract_session_cookies()
            session_manager.update_and_push(new_cookies)
        elif result != "2FA_REQUIRED":
            logger.warning("Mostaql login failed — running in PUBLIC mode.")

    # ── 5. AI Generator ────────────────────────────────────────────────────────
    generator = GeminiProposalGenerator(
        api_keys=[settings.gemini_api_key, settings.gemini_fallback_api_key],
        model_name=settings.gemini_model,
        base_proposal_prompt=settings.base_proposal_prompt,
    )

    # ── 6. Applicator ──────────────────────────────────────────────────────────
    applicator = MostaqlApplicator(session_cookies=session_manager.cookies)
    if session_manager.has_cookies:
        logger.info("  Applicator    : ENABLED ✅")
    else:
        logger.info("  Applicator    : ENABLED (awaiting cookies)")

    # Register live references in session_manager for push-updates after /refresh_session
    session_manager.register(scraper=scraper, applicator=applicator)

    # ── 5. Telegram Bot ────────────────────────────────────────────────────────
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # Restrict access to the owner's chat ID
    dp.update.middleware(AccessMiddleware(settings.telegram_chat_id))

    # Pass db and scheduler reference into handlers via workflow_data
    dp.include_router(handlers.router)

    # Inject dependencies for handlers
    dp.workflow_data.update({
        "scraper": scraper,
        "generator": generator,
        "db": db,
        "applicator": applicator,
        "session_manager": session_manager,
        "scheduler": None  # Will be updated after scheduler initialization
    })

    # ── 6. Job Monitor + Scheduler ─────────────────────────────────────────────
    monitor = JobMonitor(
        settings=settings,
        bot=bot,
        db=db,
        scraper=scraper,
        generator=generator,
    )

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        monitor.run_cycle,
        trigger="interval",
        seconds=settings.poll_interval_seconds,
        id="mostaql_monitor",
        max_instances=1,           # prevent overlapping cycles
        misfire_grace_time=10,
    )

    # Inject scheduler into handlers for pause/resume
    dp["scheduler"] = scheduler

    scheduler.start()
    logger.info(
        f"✅ Scheduler started — monitoring every {settings.poll_interval_seconds}s."
    )

    # Run one cycle immediately so you don't wait 30s for the first notification
    asyncio.create_task(monitor.run_cycle())

    # Notify the owner that the bot started
    try:
        await bot.send_message(
            chat_id=settings.telegram_chat_id,
            text=(
                "🤖 <b>بوت مستقل يعمل الآن!</b>\n"
                f"🔄 يراقب المشاريع كل {settings.poll_interval_seconds} ثانية.\n"
                f"🌐 <a href='{settings.mostaql_jobs_url}'>رابط المراقبة</a>"
            ),
        )
    except Exception as exc:
        logger.warning(f"Could not send startup message: {exc}")

    # ── 6.5 Start Dummy Web Server (For Render Free Tier) ──────────────────────
    async def handle_ping(request):
        return web.Response(text="Bot is running!")

    app = web.Application()
    app.router.add_get('/', handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logger.info(f"✅ Dummy web server started on port {port} to satisfy Render health checks.")

    # ── 7. Start polling ───────────────────────────────────────────────────────
    logger.info("Starting Telegram polling...")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        # ── Graceful shutdown ──────────────────────────────────────────────────
        logger.info("Shutting down...")
        scheduler.shutdown(wait=False)
        await scraper.__aexit__(None, None, None)
        await db.close()
        await bot.session.close()
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    asyncio.run(main())
