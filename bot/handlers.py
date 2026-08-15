"""
bot/handlers.py — Telegram command and callback handlers.

Commands:
  /start   → Welcome message + current status
  /status  → Live stats + pause/resume buttons
  /pause   → Pause job monitoring
  /resume  → Resume job monitoring

Callbacks:
  gen_proposal:{job_id}   → Generate AI proposal
  rate_project:{job_id}   → Rate the project
  apply_start:{job_id}    → Start apply flow (price → duration → questions → confirm)
  apply_confirm:{job_id}  → Final submission to mostaql.com/bid
  apply_cancel            → Cancel apply flow
  pause / resume          → Inline button equivalents
"""
from __future__ import annotations

import logging
import html
import re

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ErrorEvent, Message

from bot.keyboards import (
    apply_confirm_keyboard,
    proposal_keyboard,
    rating_result_keyboard,
    status_keyboard,
    job_keyboard,
)
from bot.states import ApplyFlow
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
    # Convert *bullet* to • bullet
    text = re.sub(r'^\* ', r'• ', text, flags=re.MULTILINE)
    return text


# ── Global error handler ──────────────────────────────────────────────────────
@router.error()
async def handle_error(event: ErrorEvent) -> None:
    exc = event.exception
    if isinstance(exc, TelegramBadRequest) and "query is too old" in str(exc):
        return
    logger.error(f"Unhandled error: {exc}", exc_info=exc)


_PAUSED = False


def is_paused() -> bool:
    return _PAUSED


# ── /start ───────────────────────────────────────────────────────────────────
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


# ── /test ───────────────────────────────────────────────────────────────────
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
        await scraper.fetch_job_details(test_job)

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


# ── /status ──────────────────────────────────────────────────────────────────
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


# ── /pause ───────────────────────────────────────────────────────────────────
@router.message(Command("pause"))
async def cmd_pause(message: Message, scheduler=None) -> None:
    global _PAUSED
    _PAUSED = True
    if scheduler:
        scheduler.pause()
    await message.answer("⏸ المراقبة متوقفة. أرسل /resume للاستئناف.")


# ── /resume ──────────────────────────────────────────────────────────────────
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
    state: FSMContext,
    db=None,
    scraper=None,
    generator=None
) -> None:
    job_id = callback.data.split(":", 1)[1]

    if not (db and scraper and generator):
        await callback.answer("⚠️ البوت غير مهيأ حالياً.")
        return

    await callback.answer("جاري كتابة العرض... ⏳")
    msg = await callback.message.reply(
        "🤖 <b>جاري تحليل المشروع وكتابة العرض المخصص...</b>",
        parse_mode="HTML"
    )

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

        # Save the raw markdown proposal for the apply flow
        await state.update_data(current_proposal=proposal)

        await msg.edit_text(
            f"📋 <b>العرض المقترح:</b>\n\n{proposal_html}",
            parse_mode="HTML",
            reply_markup=proposal_keyboard(job_id),
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
    msg = await callback.message.reply(
        "📊 <b>جاري تحليل المشروع لتقييمه كفرصة عمل...</b>",
        parse_mode="HTML"
    )

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


# ── Callback: rewrite_proposal ───────────────────────────────────────────────
@router.callback_query(F.data.startswith("rewrite_proposal:"))
async def cb_rewrite_proposal(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    job_id = callback.data.split(":", 1)[1]
    await state.set_state(ApplyFlow.waiting_proposal_note)
    await state.update_data(job_id=job_id)
    
    await callback.message.reply(
        "✏️ <b>أرسل ملاحظاتك الآن...</b>\n"
        "سيقوم الذكاء الاصطناعي بإعادة كتابة العرض بناءً عليها.",
        parse_mode="HTML"
    )
    await callback.answer()


@router.message(ApplyFlow.waiting_proposal_note)
async def process_rewrite_note(
    message: Message,
    state: FSMContext,
    db=None,
    scraper=None,
    generator=None,
) -> None:
    data = await state.get_data()
    job_id = data.get("job_id")
    user_note = message.text.strip() if message.text else ""
    
    await state.clear()
    
    if not (db and scraper and generator and job_id):
        await message.answer("⚠️ حدث خطأ أو البوت غير مهيأ.")
        return
        
    msg = await message.reply("🤖 <b>جاري إعادة صياغة العرض مع ملاحظاتك...</b>", parse_mode="HTML")
    
    try:
        url = await db.get_job_url(job_id)
        if not url:
            await msg.edit_text("⚠️ لم يتم العثور على رابط المشروع في قاعدة البيانات.")
            return

        from scraper.models import Job
        job = Job(id=job_id, url=url)
        await scraper.fetch_job_details(job)

        proposal = await generator.generate(job, user_notes=user_note)
        if not proposal:
            await msg.edit_text("⚠️ تعذر توليد العرض.")
            return

        proposal_html = markdown_to_telegram_html(proposal)

        # Save the raw markdown proposal for the apply flow
        await state.update_data(current_proposal=proposal)

        await msg.edit_text(
            f"📋 <b>العرض المعدل:</b>\n\n{proposal_html}",
            parse_mode="HTML",
            reply_markup=proposal_keyboard(job_id),
        )
    except Exception as e:
        logger.error(f"Error rewriting proposal for {job_id}: {e}", exc_info=True)
        await msg.edit_text("⚠️ حدث خطأ أثناء محاولة إعادة كتابة العرض.")


# ════════════════════════════════════════════════════════════════════════════
#  APPLY FLOW — Multi-step FSM
# ════════════════════════════════════════════════════════════════════════════

# ── Step 1: Start apply flow ─────────────────────────────────────────────────
@router.callback_query(F.data.startswith("apply_start:"))
async def cb_apply_start(
    callback: CallbackQuery,
    state: FSMContext,
    db=None,
    scraper=None,
    applicator=None,
) -> None:
    job_id = callback.data.split(":", 1)[1]

    if not applicator:
        await callback.answer("⚠️ خاصية التقديم التلقائي غير مفعّلة.")
        return

    await callback.answer()

    # Fetch form data to get questions
    url = await db.get_job_url(job_id) if db else None
    if not url:
        await callback.message.reply("⚠️ لم يتم العثور على رابط المشروع.")
        return

    wait_msg = await callback.message.reply(
        "🔍 <b>جاري جلب بيانات الفورم من المشروع...</b>",
        parse_mode="HTML"
    )

    form_data = await applicator.get_form_data(url)
    if not form_data:
        await wait_msg.edit_text("⚠️ تعذر جلب بيانات الفورم. تأكد من أن الجلسة لا تزال صالحة.")
        return

    # Retrieve the saved proposal text
    data = await state.get_data()
    proposal_text = data.get("current_proposal", "")

    # Save state data for apply flow
    await state.set_state(ApplyFlow.waiting_price)
    await state.update_data(
        job_id=job_id,
        job_url=url,
        questions=form_data.get("questions", []),
        question_index=0,
        question_answers={},
        cost="",
        period="",
        details=proposal_text,
    )

    questions = form_data.get("questions", [])
    q_note = f"\n📝 يوجد {len(questions)} سؤال إضافي سيُطلب منك الإجابة عليه بعد ذلك." if questions else ""

    await wait_msg.edit_text(
        f"✅ <b>بدأت عملية تقديم العرض</b>{q_note}\n\n"
        "💰 <b>ما السعر الذي تريد تقديمه؟</b> (بالدولار، أرسل رقماً فقط)\n"
        "<i>مثال: 150</i>",
        parse_mode="HTML"
    )


# ── Step 2: Receive price ─────────────────────────────────────────────────────
@router.message(ApplyFlow.waiting_price)
async def apply_got_price(message: Message, state: FSMContext) -> None:
    price_text = message.text.strip() if message.text else ""

    if not price_text.replace(".", "").isdigit():
        await message.answer("⚠️ أرسل رقماً صحيحاً فقط. مثال: <code>150</code>", parse_mode="HTML")
        return

    await state.update_data(cost=price_text)
    await state.set_state(ApplyFlow.waiting_duration)

    await message.answer(
        f"✅ السعر: <b>{price_text}$</b>\n\n"
        "⏱ <b>كم مدة التسليم؟</b> (بالأيام، أرسل رقماً فقط)\n"
        "<i>مثال: 7</i>",
        parse_mode="HTML"
    )


# ── Step 3: Receive duration ──────────────────────────────────────────────────
@router.message(ApplyFlow.waiting_duration)
async def apply_got_duration(message: Message, state: FSMContext) -> None:
    duration_text = message.text.strip() if message.text else ""

    if not duration_text.isdigit():
        await message.answer("⚠️ أرسل رقماً صحيحاً فقط. مثال: <code>7</code>", parse_mode="HTML")
        return

    await state.update_data(period=duration_text)
    data = await state.get_data()
    questions = data.get("questions", [])

    if questions:
        # Move to questions
        await state.set_state(ApplyFlow.waiting_question)
        await state.update_data(question_index=0)
        first_q = questions[0]["text"]
        await message.answer(
            f"✅ مدة التسليم: <b>{duration_text} يوم</b>\n\n"
            f"❓ <b>سؤال 1/{len(questions)}:</b>\n{first_q}",
            parse_mode="HTML"
        )
    else:
        # No questions — go straight to confirm
        await _show_confirmation(message, state)


# ── Step 4: Receive question answers ─────────────────────────────────────────
@router.message(ApplyFlow.waiting_question)
async def apply_got_question_answer(message: Message, state: FSMContext) -> None:
    answer = message.text.strip() if message.text else ""
    if not answer:
        await message.answer("⚠️ الرجاء إرسال إجابة غير فارغة.")
        return

    data = await state.get_data()
    questions = data.get("questions", [])
    q_index = data.get("question_index", 0)
    answers = data.get("question_answers", {})

    current_q = questions[q_index]
    answers[current_q["id"]] = answer
    await state.update_data(question_answers=answers)

    next_index = q_index + 1
    if next_index < len(questions):
        await state.update_data(question_index=next_index)
        next_q = questions[next_index]
        await message.answer(
            f"✅ تم حفظ الإجابة.\n\n"
            f"❓ <b>سؤال {next_index + 1}/{len(questions)}:</b>\n{next_q['text']}",
            parse_mode="HTML"
        )
    else:
        # All questions answered — show confirmation
        await _show_confirmation(message, state)


async def _show_confirmation(message: Message, state: FSMContext) -> None:
    """Show a summary of all collected data and ask for confirmation."""
    data = await state.get_data()
    job_id = data["job_id"]
    cost = data["cost"]
    period = data["period"]
    answers = data.get("question_answers", {})
    questions = data.get("questions", [])

    # Build answers summary
    q_summary = ""
    for q in questions:
        q_id = q["id"]
        ans = answers.get(q_id, "—")
        q_summary += f"\n❓ <b>{q['text']}</b>\n💬 {ans}\n"

    summary = (
        f"📋 <b>ملخص العرض قبل الإرسال:</b>\n\n"
        f"💰 <b>السعر:</b> {cost}$\n"
        f"⏱ <b>مدة التسليم:</b> {period} يوم\n"
        f"{q_summary}\n"
        f"هل تريد إرسال العرض الآن؟"
    )

    await state.set_state(ApplyFlow.waiting_confirm)
    await message.answer(summary, parse_mode="HTML", reply_markup=apply_confirm_keyboard(job_id))


# ── Step 5: Final confirm ─────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("apply_confirm:"))
async def cb_apply_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    applicator=None,
) -> None:
    await callback.answer()
    data = await state.get_data()
    await state.clear()

    if not applicator:
        await callback.message.edit_text("⚠️ خاصية التقديم التلقائي غير مفعّلة.")
        return

    msg = await callback.message.edit_text(
        "🚀 <b>جاري إرسال العرض إلى مستقل...</b>",
        parse_mode="HTML",
        reply_markup=None,
    )

    from bot.applicator import JobApplyData
    apply_data = JobApplyData(
        job_id=data["job_id"],
        job_url=data["job_url"],
        cost=data["cost"],
        period=data["period"],
        details=data.get("details", ""),
        question_answers=data.get("question_answers", {}),
    )

    result = await applicator.submit(apply_data)

    status_icon = "✅" if result.success else "❌"
    await msg.edit_text(
        f"{status_icon} <b>{'تم الإرسال!' if result.success else 'فشل الإرسال'}</b>\n\n"
        f"{result.message}",
        parse_mode="HTML",
    )


# ── Cancel apply flow ─────────────────────────────────────────────────────────
@router.callback_query(F.data == "apply_cancel")
async def cb_apply_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("تم الإلغاء.")
    await callback.message.edit_text("❌ <b>تم إلغاء عملية التقديم.</b>", parse_mode="HTML", reply_markup=None)


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
