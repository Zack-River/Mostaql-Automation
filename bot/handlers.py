"""
bot/handlers.py — Telegram command and callback handlers.

Commands:
  /start   → Welcome message + current status
  /status  → Live stats + pause/resume buttons
  /pause   → Pause job monitoring
  /resume  → Resume job monitoring

Callbacks:
  copy_proposal:{job_id}  → Send the stored proposal as a plain-text message
  pause / resume          → Inline button equivalents
"""
from __future__ import annotations

import logging
import html
import re

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, ErrorEvent, Message

from bot.keyboards import status_keyboard, job_keyboard, rating_result_keyboard
from scheduler.job_monitor import format_notification

logger = logging.getLogger(__name__)
router = Router()

def markdown_to_telegram_html(text: str) -> str:
    """Converts Gemini Markdown output to Telegram-friendly HTML."""
    if not text:
        return ""
    # Escape HTML tags first
    text = html.escape(text)
    # Convert **bold** to <b>bold</b>
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    # Convert ## Header to <b>Header</b>
    text = re.sub(r'^## (.*)', r'<b>\1</b>', text, flags=re.MULTILINE)
    # Convert # Header to <b>Header</b>
    text = re.sub(r'^# (.*)', r'<b>\1</b>', text, flags=re.MULTILINE)
    # Convert *bullet* to • bullet (since * italic might break, let's just do lists)
    text = re.sub(r'^\* ', r'• ', text, flags=re.MULTILINE)
    return text

# ── Global error handler — silently ignore expired callback queries ────────────
@router.error()
async def handle_error(event: ErrorEvent) -> None:
    exc = event.exception
    if isinstance(exc, TelegramBadRequest) and "query is too old" in str(exc):
        return  # harmless — old inline button clicked after bot restart
    logger.error(f"Unhandled error: {exc}", exc_info=exc)

# These are injected at startup by main.py via bot_data / FSM context
# We access them through the dispatcher's workflow_data
_PAUSED = False


def is_paused() -> bool:
    return _PAUSED


# ── /start ────────────────────────────────────────────────────────────────────
@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "🤖 <b>مرحباً! بوت مستقل للمشاريع البرمجية</b>\n\n"
        "أنا أراقب منصة مستقل كل 30 ثانية وأُرسل إليك المشاريع الجديدة "
        "في تصنيف البرمجة فور نشرها.\n\n"
        "الأوامر المتاحة:\n"
        "  /test   — جلب أحدث مشروع واختبار توليد العرض\n"
        "  /status — إحصائيات المراقبة\n"
        "  /pause  — إيقاف مؤقت\n"
        "  /resume — استئناف\n\n"
        "✅ البوت يعمل الآن.",
        parse_mode="HTML",
    )

# ── /test ─────────────────────────────────────────────────────────────────────
@router.message(Command("test"))
async def cmd_test(message: Message, scraper=None, db=None) -> None:
    if not scraper:
        await message.answer("⚠️ السكريبر غير متصل حالياً. حاول مجدداً.")
        return
        
    wait_msg = await message.answer("🔍 جاري جلب أحدث مشروع من مستقل لاختبار البوت...")
    try:
        jobs = await scraper.fetch_jobs_list()
        if not jobs:
            await wait_msg.edit_text("⚠️ لم أتمكن من العثور على أي مشاريع حالياً.")
            return
            
        test_job = jobs[0]
        # Fetch full details
        await scraper.fetch_job_details(test_job)
        
        # Save to DB so the callback can find its URL
        if db:
            await db.mark_job_seen(test_job.id, title=test_job.title, url=test_job.url)
            
        message_text = format_notification(test_job)
        keyboard = job_keyboard(job_url=test_job.url, job_id=test_job.id)
        
        await wait_msg.edit_text(
            text=message_text,
            parse_mode="HTML",
            reply_markup=keyboard,
            disable_web_page_preview=True,
        )
    except Exception as exc:
        logger.error(f"Error in /test command: {exc}", exc_info=True)
        await wait_msg.edit_text(f"⚠️ حدث خطأ أثناء جلب المشروع: {exc}")


# ── /status ───────────────────────────────────────────────────────────────────
@router.message(Command("status"))
async def cmd_status(message: Message, db=None) -> None:
    state_icon = "⏸" if _PAUSED else "🟢"
    state_text = "متوقف مؤقتاً" if _PAUSED else "يعمل"

    stats_text = ""
    if db:
        stats = await db.get_stats()
        total_notified = stats.get("total_notified", "0")
        last_checked   = stats.get("last_checked", "لم يتم الفحص بعد")
        total_seen     = stats.get("total_seen", "0")
        stats_text = (
            f"\n\n📊 <b>الإحصائيات:</b>\n"
            f"  • إجمالي الإشعارات المُرسَلة: {total_notified}\n"
            f"  • إجمالي المشاريع المتبعة: {total_seen}\n"
            f"  • آخر فحص: {last_checked}"
        )

    await message.answer(
        f"{state_icon} <b>حالة البوت: {state_text}</b>{stats_text}",
        parse_mode="HTML",
        reply_markup=status_keyboard(),
    )


# ── /pause ────────────────────────────────────────────────────────────────────
@router.message(Command("pause"))
async def cmd_pause(message: Message, scheduler=None) -> None:
    global _PAUSED
    _PAUSED = True
    if scheduler:
        scheduler.pause()
    await message.answer("⏸ المراقبة متوقفة. أرسل /resume للاستئناف.")


# ── /resume ───────────────────────────────────────────────────────────────────
@router.message(Command("resume"))
async def cmd_resume(message: Message, scheduler=None) -> None:
    global _PAUSED
    _PAUSED = False
    if scheduler:
        scheduler.resume()
    await message.answer("▶️ المراقبة مستأنفة.")


# ── Callback: gen_proposal ───────────────────────────────────────────────────
@router.callback_query(F.data.startswith("gen_proposal:"))
async def cb_gen_proposal(
    callback: CallbackQuery,
    db=None,
    scraper=None,
    generator=None
) -> None:
    job_id = callback.data.split(":", 1)[1]
    
    if not (db and scraper and generator):
        await callback.answer("⚠️ البوت غير مهيأ حالياً.")
        return
        
    await callback.answer("جاري كتابة العرض... ⏳")
    
    msg = await callback.message.reply("🤖 <b>جاري تحليل المشروع وكتابة العرض المخصص...</b>", parse_mode="HTML")
    
    try:
        url = await db.get_job_url(job_id)
        if not url:
            await msg.edit_text("⚠️ لم يتم العثور على رابط المشروع في قاعدة البيانات.")
            return
            
        from scraper.models import Job
        job = Job(id=job_id, url=url)
        await scraper.fetch_job_details(job)
        
        proposal = await generator.generate(job)
        if not proposal:
            await msg.edit_text("⚠️ تعذر توليد العرض.")
            return
            
        proposal_html = markdown_to_telegram_html(proposal)
        
        await msg.edit_text(
            f"📋 <b>العرض المقترح:</b>\n\n{proposal_html}",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error generating proposal for {job_id}: {e}", exc_info=True)
        await msg.edit_text("⚠️ حدث خطأ أثناء محاولة توليد العرض.")


# ── Callback: rate_project ───────────────────────────────────────────────────
@router.callback_query(F.data.startswith("rate_project:"))
async def cb_rate_project(
    callback: CallbackQuery,
    db=None,
    scraper=None,
    generator=None
) -> None:
    job_id = callback.data.split(":", 1)[1]
    
    if not (db and scraper and generator):
        await callback.answer("⚠️ البوت غير مهيأ حالياً.")
        return
        
    await callback.answer("جاري تقييم المشروع... ⏳")
    
    msg = await callback.message.reply("📊 <b>جاري تحليل المشروع لتقييمه كفرصة عمل...</b>", parse_mode="HTML")
    
    try:
        url = await db.get_job_url(job_id)
        if not url:
            await msg.edit_text("⚠️ لم يتم العثور على رابط المشروع في قاعدة البيانات.")
            return
            
        from scraper.models import Job
        job = Job(id=job_id, url=url)
        await scraper.fetch_job_details(job)
        
        rating = await generator.rate_job(job)
        if not rating:
            await msg.edit_text("⚠️ تعذر تقييم المشروع.")
            return
            
        rating_html = markdown_to_telegram_html(rating)
        
        await msg.edit_text(
            f"📋 <b>تقييم المشروع:</b>\n\n{rating_html}",
            parse_mode="HTML",
            reply_markup=rating_result_keyboard(job_id)
        )
    except Exception as e:
        logger.error(f"Error rating job {job_id}: {e}", exc_info=True)
        await msg.edit_text("⚠️ حدث خطأ أثناء محاولة تقييم المشروع.")


# ── Callback: pause/resume from inline buttons ────────────────────────────────
@router.callback_query(F.data == "pause")
async def cb_pause(callback: CallbackQuery, scheduler=None) -> None:
    global _PAUSED
    _PAUSED = True
    if scheduler:
        scheduler.pause()
    await callback.answer("⏸ تم الإيقاف المؤقت.")
    await callback.message.edit_reply_markup(reply_markup=status_keyboard())


@router.callback_query(F.data == "resume")
async def cb_resume(callback: CallbackQuery, scheduler=None) -> None:
    global _PAUSED
    _PAUSED = False
    if scheduler:
        scheduler.resume()
    await callback.answer("▶️ تم الاستئناف.")
    await callback.message.edit_reply_markup(reply_markup=status_keyboard())
