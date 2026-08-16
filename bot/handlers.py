"""
bot/handlers.py — Telegram command and callback handlers.

Commands:
  /start   → Welcome message
  /test    → Fetch latest job and show notification
  /status  → Live stats + pause/resume buttons
  /pause   → Pause job monitoring
  /resume  → Resume job monitoring

Apply Flows:
  ⚡ Quick Apply:
    quick_apply:{job_id}  → AI fills price/duration/questions → summary → confirm → submit
  🛠 Custom Apply:
    custom_apply:{job_id} → step-by-step, each step shows AI default + option to type custom
    use_default:price:{job_id}      → accept AI-suggested price
    use_default:duration:{job_id}   → accept AI-suggested duration
    use_default:question:{i}:{job_id} → accept AI-suggested answer for question i

  apply_confirm:{job_id} → submit to mostaql.com/bid
  apply_cancel           → cancel any flow
"""
from __future__ import annotations

import html
import logging
import re

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ErrorEvent, Message

from bot.keyboards import (
    apply_confirm_keyboard,
    duration_step_keyboard,
    price_step_keyboard,
    proposal_keyboard,
    question_step_keyboard,
    rating_result_keyboard,
    status_keyboard,
    job_keyboard,
)
from bot.states import ApplyFlow
from scheduler.job_monitor import format_notification

logger = logging.getLogger(__name__)
router = Router()

_PAUSED = False


def is_paused() -> bool:
    return _PAUSED


# ── Helpers ──────────────────────────────────────────────────────────────────

def markdown_to_telegram_html(text: str) -> str:
    """Converts Gemini Markdown output to Telegram-friendly HTML."""
    if not text:
        return ""
    text = html.escape(text)
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'^## (.*)', r'<b>\1</b>', text, flags=re.MULTILINE)
    text = re.sub(r'^# (.*)', r'<b>\1</b>', text, flags=re.MULTILINE)
    text = re.sub(r'^\* ', r'• ', text, flags=re.MULTILINE)
    return text


def _build_confirm_summary(data: dict) -> str:
    """Builds a human-readable summary for the final confirm step."""
    job_id  = data.get("job_id", "")
    cost    = data.get("cost", "—")
    period  = data.get("period", "—")
    answers = data.get("question_answers", {})
    questions = data.get("form_questions", [])

    q_lines = ""
    for q in questions:
        q_id  = q.get("id", "")
        q_txt = q.get("text", "")
        ans   = answers.get(q_id, "—")
        q_lines += f"\n❓ <b>{q_txt}</b>\n💬 {ans}\n"

    return (
        f"📋 <b>ملخص العرض قبل الإرسال:</b>\n\n"
        f"💰 <b>السعر:</b> {cost}$\n"
        f"⏱ <b>مدة التسليم:</b> {period} يوم\n"
        f"{q_lines}\n"
        f"هل تريد إرسال العرض الآن؟"
    )


async def _fetch_form_and_suggestions(
    job_id: str,
    url: str,
    scraper,
    generator,
    applicator,
) -> tuple[dict | None, dict]:
    """
    Fetches form data from mostaql and gets AI suggestions.
    Returns (form_data, suggestions). Either can be {} on failure.
    """
    from scraper.models import Job
    job = Job(id=job_id, url=url)
    await scraper.fetch_job_details(job)

    form_data = await applicator.get_form_data(url) if applicator else None
    if not form_data:
        return None, {}

    form_questions = form_data.get("questions", [])
    suggestions = await generator.suggest_apply_params(job, form_questions)
    return form_data, suggestions


# ── Error handler ─────────────────────────────────────────────────────────────
@router.error()
async def handle_error(event: ErrorEvent) -> None:
    exc = event.exception
    if isinstance(exc, TelegramBadRequest) and "query is too old" in str(exc):
        return
    logger.error(f"Unhandled error: {exc}", exc_info=exc)


# ── /start ────────────────────────────────────────────────────────────────────
@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    await message.answer(
        "🤖 <b>مرحباً! بوت مستقل للمشاريع البرمجية</b>\n\n"
        "أراقب منصة مستقل كل 30 ثانية وأُرسل إليك المشاريع الجديدة فور نشرها.\n\n"
        "الأوامر:\n"
        "  /test   — اختبار بأحدث مشروع\n"
        "  /status — إحصائيات\n"
        "  /pause  — إيقاف مؤقت\n"
        "  /resume — استئناف\n\n"
        "✅ البوت يعمل الآن.",
        parse_mode="HTML",
    )


# ── /test ─────────────────────────────────────────────────────────────────────
@router.message(Command("test"))
async def cmd_test(message: Message, scraper=None, db=None) -> None:
    if not scraper:
        await message.answer("⚠️ السكريبر غير متصل.")
        return
    wait = await message.answer("🔍 جاري جلب أحدث مشروع...")
    try:
        jobs = await scraper.fetch_jobs_list()
        if not jobs:
            await wait.edit_text("⚠️ لم أعثر على أي مشاريع.")
            return
        job = jobs[0]
        await scraper.fetch_job_details(job)
        if db:
            await db.mark_job_seen(job.id, title=job.title, url=job.url)
        await wait.edit_text(
            text=format_notification(job),
            parse_mode="HTML",
            reply_markup=job_keyboard(job_url=job.url, job_id=job.id),
            disable_web_page_preview=True,
        )
    except Exception as exc:
        logger.error(f"/test error: {exc}", exc_info=True)
        await wait.edit_text(f"⚠️ حدث خطأ: {exc}")


# ── /status ───────────────────────────────────────────────────────────────────
@router.message(Command("status"))
async def cmd_status(message: Message, db=None) -> None:
    state_icon = "⏸" if _PAUSED else "🟢"
    state_text = "متوقف مؤقتاً" if _PAUSED else "يعمل"
    stats_text = ""
    if db:
        stats = await db.get_stats()
        stats_text = (
            f"\n\n📊 <b>الإحصائيات:</b>\n"
            f"  • إجمالي الإشعارات: {stats.get('total_notified','0')}\n"
            f"  • المشاريع المتبعة: {stats.get('total_seen','0')}\n"
            f"  • آخر فحص: {stats.get('last_checked','—')}"
        )
    await message.answer(
        f"{state_icon} <b>حالة البوت: {state_text}</b>{stats_text}",
        parse_mode="HTML",
        reply_markup=status_keyboard(),
    )


# ── /pause & /resume ──────────────────────────────────────────────────────────
@router.message(Command("pause"))
async def cmd_pause(message: Message, scheduler=None) -> None:
    global _PAUSED
    _PAUSED = True
    if scheduler:
        scheduler.pause()
    await message.answer("⏸ المراقبة متوقفة. أرسل /resume للاستئناف.")


@router.message(Command("resume"))
async def cmd_resume(message: Message, scheduler=None) -> None:
    global _PAUSED
    _PAUSED = False
    if scheduler:
        scheduler.resume()
    await message.answer("▶️ المراقبة مستأنفة.")


# ══════════════════════════════════════════════════════════════════════════════
#  PROPOSAL GENERATION
# ══════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("gen_proposal:"))
async def cb_gen_proposal(
    callback: CallbackQuery,
    state: FSMContext,
    db=None, scraper=None, generator=None
) -> None:
    job_id = callback.data.split(":", 1)[1]
    if not (db and scraper and generator):
        await callback.answer("⚠️ البوت غير مهيأ.")
        return
    await callback.answer("جاري كتابة العرض... ⏳")
    msg = await callback.message.reply("🤖 <b>جاري تحليل المشروع وكتابة العرض...</b>", parse_mode="HTML")
    try:
        url = await db.get_job_url(job_id)
        if not url:
            await msg.edit_text("⚠️ لم يتم العثور على رابط المشروع.")
            return
        from scraper.models import Job
        job = Job(id=job_id, url=url)
        await scraper.fetch_job_details(job)
        proposal = await generator.generate(job)
        if not proposal:
            await msg.edit_text("⚠️ تعذر توليد العرض. تحقق من مفاتيح Gemini API.")
            return
        await state.update_data(current_proposal=proposal)
        await msg.edit_text(
            f"📋 <b>العرض المقترح:</b>\n\n{markdown_to_telegram_html(proposal)}",
            parse_mode="HTML",
            reply_markup=proposal_keyboard(job_id),
        )
    except Exception as e:
        logger.error(f"gen_proposal error for {job_id}: {e}", exc_info=True)
        await msg.edit_text("⚠️ حدث خطأ أثناء توليد العرض.")


@router.callback_query(F.data.startswith("rate_project:"))
async def cb_rate_project(
    callback: CallbackQuery,
    db=None, scraper=None, generator=None
) -> None:
    job_id = callback.data.split(":", 1)[1]
    if not (db and scraper and generator):
        await callback.answer("⚠️ البوت غير مهيأ.")
        return
    await callback.answer("جاري تقييم المشروع... ⏳")
    msg = await callback.message.reply("📊 <b>جاري تحليل المشروع لتقييمه...</b>", parse_mode="HTML")
    try:
        url = await db.get_job_url(job_id)
        if not url:
            await msg.edit_text("⚠️ لم يتم العثور على رابط المشروع.")
            return
        from scraper.models import Job
        job = Job(id=job_id, url=url)
        await scraper.fetch_job_details(job)
        rating = await generator.rate_job(job)
        if not rating:
            await msg.edit_text("⚠️ تعذر تقييم المشروع. تحقق من مفاتيح Gemini API.")
            return
        await msg.edit_text(
            f"📊 <b>تقييم المشروع:</b>\n\n{markdown_to_telegram_html(rating)}",
            parse_mode="HTML",
            reply_markup=rating_result_keyboard(job_id),
        )
    except Exception as e:
        logger.error(f"rate_project error for {job_id}: {e}", exc_info=True)
        await msg.edit_text("⚠️ حدث خطأ أثناء التقييم.")


# ── Rewrite Proposal ──────────────────────────────────────────────────────────
@router.callback_query(F.data.startswith("rewrite_proposal:"))
async def cb_rewrite_proposal(callback: CallbackQuery, state: FSMContext) -> None:
    job_id = callback.data.split(":", 1)[1]
    await state.set_state(ApplyFlow.rewrite_note)
    await state.update_data(job_id=job_id)
    await callback.message.reply(
        "✏️ <b>أرسل ملاحظاتك لتعديل العرض:</b>\n"
        "مثال: <i>ركز على خبرتي في ووردبريس، اجعل العرض أقصر</i>",
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(ApplyFlow.rewrite_note)
async def process_rewrite_note(
    message: Message, state: FSMContext,
    db=None, scraper=None, generator=None
) -> None:
    data = await state.get_data()
    job_id = data.get("job_id")
    note   = message.text.strip() if message.text else ""
    await state.clear()
    if not (db and scraper and generator and job_id):
        await message.answer("⚠️ حدث خطأ.")
        return
    msg = await message.reply("🤖 <b>جاري إعادة صياغة العرض مع ملاحظاتك...</b>", parse_mode="HTML")
    try:
        url = await db.get_job_url(job_id)
        if not url:
            await msg.edit_text("⚠️ لم يتم العثور على رابط المشروع.")
            return
        from scraper.models import Job
        job = Job(id=job_id, url=url)
        await scraper.fetch_job_details(job)
        proposal = await generator.generate(job, user_notes=note)
        if not proposal:
            await msg.edit_text("⚠️ تعذر توليد العرض.")
            return
        await state.update_data(current_proposal=proposal)
        await msg.edit_text(
            f"📋 <b>العرض المعدل:</b>\n\n{markdown_to_telegram_html(proposal)}",
            parse_mode="HTML",
            reply_markup=proposal_keyboard(job_id),
        )
    except Exception as e:
        logger.error(f"rewrite_proposal error for {job_id}: {e}", exc_info=True)
        await msg.edit_text("⚠️ حدث خطأ أثناء إعادة الكتابة.")


# ══════════════════════════════════════════════════════════════════════════════
#  ⚡ QUICK APPLY FLOW
# ══════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("quick_apply:"))
async def cb_quick_apply(
    callback: CallbackQuery,
    state: FSMContext,
    db=None, scraper=None, generator=None, applicator=None,
) -> None:
    job_id = callback.data.split(":", 1)[1]
    if not applicator:
        await callback.answer("⚠️ خاصية التقديم التلقائي غير مفعّلة (لا يوجد session cookies).")
        return
    await callback.answer()

    url = await db.get_job_url(job_id) if db else None
    if not url:
        await callback.message.reply("⚠️ لم يتم العثور على رابط المشروع.")
        return

    msg = await callback.message.reply(
        "⚡ <b>جاري تحليل المشروع والحصول على الاقتراحات...</b>",
        parse_mode="HTML"
    )

    try:
        form_data, suggestions = await _fetch_form_and_suggestions(
            job_id, url, scraper, generator, applicator
        )
        if not form_data:
            await msg.edit_text("⚠️ تعذر جلب بيانات فورم التقديم. تأكد أن الجلسة لا تزال صالحة.")
            return

        form_questions = form_data.get("questions", [])
        price    = str(suggestions.get("price", ""))
        duration = str(suggestions.get("duration", ""))
        q_ans_list = suggestions.get("question_answers", [])

        # Map answers by question ID
        question_answers: dict[str, str] = {}
        for ans in q_ans_list:
            q_id = ans.get("id", "")
            if q_id:
                question_answers[q_id] = ans.get("answer", "")

        # Get proposal text from previous state
        prev = await state.get_data()
        details = prev.get("current_proposal", "")

        await state.set_state(ApplyFlow.quick_confirm)
        await state.update_data(
            job_id=job_id,
            job_url=url,
            form_questions=form_questions,
            cost=price,
            period=duration,
            question_answers=question_answers,
            details=details,
        )

        summary = _build_confirm_summary({
            "job_id": job_id,
            "cost": price or "—",
            "period": duration or "—",
            "question_answers": question_answers,
            "form_questions": form_questions,
        })

        if not price or not duration:
            summary += "\n\n⚠️ <i>لم يتمكن الذكاء الاصطناعي من اقتراح سعر/مدة. ستحتاج إلى التقديم المخصص.</i>"

        await msg.edit_text(
            f"⚡ <b>التقديم السريع — مراجعة قبل الإرسال:</b>\n\n{summary}",
            parse_mode="HTML",
            reply_markup=apply_confirm_keyboard(job_id),
        )

    except Exception as e:
        logger.error(f"quick_apply error for {job_id}: {e}", exc_info=True)
        await msg.edit_text("⚠️ حدث خطأ أثناء تهيئة التقديم السريع.")


# ══════════════════════════════════════════════════════════════════════════════
#  🛠 CUSTOM APPLY FLOW — Step by step with AI defaults
# ══════════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("custom_apply:"))
async def cb_custom_apply(
    callback: CallbackQuery,
    state: FSMContext,
    db=None, scraper=None, generator=None, applicator=None,
) -> None:
    job_id = callback.data.split(":", 1)[1]
    if not applicator:
        await callback.answer("⚠️ خاصية التقديم التلقائي غير مفعّلة.")
        return
    await callback.answer()

    url = await db.get_job_url(job_id) if db else None
    if not url:
        await callback.message.reply("⚠️ لم يتم العثور على رابط المشروع.")
        return

    msg = await callback.message.reply(
        "🛠 <b>جاري تهيئة التقديم المخصص...</b>",
        parse_mode="HTML"
    )

    try:
        form_data, suggestions = await _fetch_form_and_suggestions(
            job_id, url, scraper, generator, applicator
        )
        if not form_data:
            await msg.edit_text("⚠️ تعذر جلب بيانات فورم التقديم. تأكد أن الجلسة لا تزال صالحة.")
            return

        form_questions = form_data.get("questions", [])
        sug_price    = str(suggestions.get("price", ""))
        sug_duration = str(suggestions.get("duration", ""))
        sug_q_answers = suggestions.get("question_answers", [])

        # Save state
        prev = await state.get_data()
        details = prev.get("current_proposal", "")

        await state.set_state(ApplyFlow.custom_price)
        await state.update_data(
            job_id=job_id,
            job_url=url,
            form_questions=form_questions,
            sug_price=sug_price,
            sug_duration=sug_duration,
            sug_q_answers=sug_q_answers,
            cost="",
            period="",
            question_answers={},
            question_index=0,
            details=details,
        )

        await msg.edit_text(
            f"🛠 <b>التقديم المخصص — الخطوة 1: السعر</b>\n\n"
            f"💰 كم السعر الذي تريد تقديمه؟ (بالدولار)\n\n"
            f"<i>اكتب رقماً، أو اضغط زر القبول أدناه للموافقة على المقترح.</i>",
            parse_mode="HTML",
            reply_markup=price_step_keyboard(job_id, sug_price),
        )

    except Exception as e:
        logger.error(f"custom_apply error for {job_id}: {e}", exc_info=True)
        await msg.edit_text("⚠️ حدث خطأ أثناء تهيئة التقديم المخصص.")


# ── Custom Step: Price ────────────────────────────────────────────────────────

async def _go_to_duration_step(target, state: FSMContext, price: str) -> None:
    """Advance to duration step after price is accepted."""
    data = await state.get_data()
    job_id = data["job_id"]
    sug_duration = data.get("sug_duration", "")

    await state.update_data(cost=price)
    await state.set_state(ApplyFlow.custom_duration)

    text = (
        f"✅ السعر: <b>{price}$</b>\n\n"
        f"🛠 <b>الخطوة 2: مدة التسليم</b>\n\n"
        f"⏱ كم مدة التسليم؟ (بالأيام)\n\n"
        f"<i>اكتب رقماً، أو اضغط زر القبول أدناه.</i>"
    )
    kbd = duration_step_keyboard(job_id, sug_duration)

    if isinstance(target, Message):
        await target.answer(text, parse_mode="HTML", reply_markup=kbd)
    else:
        await target.message.edit_text(text, parse_mode="HTML", reply_markup=kbd)


@router.message(ApplyFlow.custom_price)
async def custom_price_typed(message: Message, state: FSMContext) -> None:
    price = message.text.strip() if message.text else ""
    if not price.replace(".", "").isdigit():
        await message.answer("⚠️ أرسل رقماً فقط. مثال: <code>150</code>", parse_mode="HTML")
        return
    await _go_to_duration_step(message, state, price)


@router.callback_query(F.data.startswith("use_default:price:"))
async def custom_price_default(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    price = data.get("sug_price", "")
    if not price:
        await callback.message.reply("⚠️ لا يوجد سعر مقترح. أرسل الرقم يدوياً.")
        return
    await _go_to_duration_step(callback, state, price)


# ── Custom Step: Duration ─────────────────────────────────────────────────────

async def _go_to_next_step_after_duration(target, state: FSMContext, duration: str) -> None:
    """After duration: go to first question or straight to confirm."""
    data = await state.get_data()
    await state.update_data(period=duration, question_index=0)
    form_questions = data.get("form_questions", [])

    if form_questions:
        await state.set_state(ApplyFlow.custom_question)
        await _send_question_step(target, state, 0)
    else:
        await state.set_state(ApplyFlow.custom_confirm)
        await _send_confirm(target, state)


@router.message(ApplyFlow.custom_duration)
async def custom_duration_typed(message: Message, state: FSMContext) -> None:
    duration = message.text.strip() if message.text else ""
    if not duration.isdigit():
        await message.answer("⚠️ أرسل رقماً فقط. مثال: <code>7</code>", parse_mode="HTML")
        return
    await _go_to_next_step_after_duration(message, state, duration)


@router.callback_query(F.data.startswith("use_default:duration:"))
async def custom_duration_default(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    data = await state.get_data()
    duration = data.get("sug_duration", "")
    if not duration:
        await callback.message.reply("⚠️ لا توجد مدة مقترحة. أرسل الرقم يدوياً.")
        return
    await _go_to_next_step_after_duration(callback, state, duration)


# ── Custom Step: Questions ────────────────────────────────────────────────────

async def _send_question_step(target, state: FSMContext, q_index: int) -> None:
    data = await state.get_data()
    form_questions = data.get("form_questions", [])
    sug_q_answers  = data.get("sug_q_answers", [])
    job_id = data["job_id"]
    total  = len(form_questions)
    q      = form_questions[q_index]
    q_text = q.get("text", f"سؤال {q_index + 1}")

    # Find suggested answer for this question
    sug_answer = ""
    for ans in sug_q_answers:
        if ans.get("index") == q_index:
            sug_answer = ans.get("answer", "")
            break

    kbd  = question_step_keyboard(job_id, q_index, sug_answer)
    sug_preview = f"\n\n💡 <b>الإجابة المقترحة:</b>\n<i>{html.escape(sug_answer[:300])}</i>" if sug_answer else ""
    text = (
        f"🛠 <b>الخطوة {q_index + 3}: سؤال {q_index + 1}/{total}</b>\n\n"
        f"❓ {html.escape(q_text)}"
        f"{sug_preview}\n\n"
        f"<i>اكتب إجابتك، أو اضغط 'قبول المقترح' أدناه.</i>"
    )

    if isinstance(target, Message):
        await target.answer(text, parse_mode="HTML", reply_markup=kbd)
    else:
        await target.message.edit_text(text, parse_mode="HTML", reply_markup=kbd)


async def _save_question_answer_and_advance(target, state: FSMContext, q_index: int, answer: str) -> None:
    data = await state.get_data()
    form_questions   = data.get("form_questions", [])
    question_answers = data.get("question_answers", {})

    q_id = form_questions[q_index].get("id", "") if q_index < len(form_questions) else ""
    if q_id:
        question_answers[q_id] = answer
    await state.update_data(question_answers=question_answers)

    next_index = q_index + 1
    if next_index < len(form_questions):
        await state.update_data(question_index=next_index)
        await _send_question_step(target, state, next_index)
    else:
        await state.set_state(ApplyFlow.custom_confirm)
        await _send_confirm(target, state)


@router.message(ApplyFlow.custom_question)
async def custom_question_typed(message: Message, state: FSMContext) -> None:
    answer = message.text.strip() if message.text else ""
    if not answer:
        await message.answer("⚠️ الرجاء إرسال إجابة غير فارغة.")
        return
    data    = await state.get_data()
    q_index = data.get("question_index", 0)
    await _save_question_answer_and_advance(message, state, q_index, answer)


@router.callback_query(F.data.startswith("use_default:question:"))
async def custom_question_default(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    parts   = callback.data.split(":")  # use_default:question:{index}:{job_id}
    q_index = int(parts[3]) if len(parts) > 3 else 0

    data = await state.get_data()
    sug_q_answers = data.get("sug_q_answers", [])
    answer = ""
    for ans in sug_q_answers:
        if ans.get("index") == q_index:
            answer = ans.get("answer", "")
            break

    if not answer:
        await callback.message.reply("⚠️ لا توجد إجابة مقترحة. اكتب إجابتك يدوياً.")
        return

    await _save_question_answer_and_advance(callback, state, q_index, answer)


# ── Confirm step (shared by both flows) ──────────────────────────────────────

async def _send_confirm(target, state: FSMContext) -> None:
    data = await state.get_data()
    job_id = data["job_id"]
    summary = _build_confirm_summary(data)
    text = f"✅ <b>مراجعة نهائية قبل الإرسال:</b>\n\n{summary}"
    kbd  = apply_confirm_keyboard(job_id)

    if isinstance(target, Message):
        await target.answer(text, parse_mode="HTML", reply_markup=kbd)
    else:
        await target.message.edit_text(text, parse_mode="HTML", reply_markup=kbd)


# ── Final submission ──────────────────────────────────────────────────────────

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
        await callback.message.edit_text("⚠️ خاصية التقديم التلقائي غير مفعّلة.", reply_markup=None)
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
        cost=data.get("cost", ""),
        period=data.get("period", ""),
        details=data.get("details", ""),
        question_answers=data.get("question_answers", {}),
    )

    result = await applicator.submit(apply_data)
    icon = "✅" if result.success else "❌"
    await msg.edit_text(
        f"{icon} <b>{'تم الإرسال!' if result.success else 'فشل الإرسال'}</b>\n\n{result.message}",
        parse_mode="HTML",
    )


# ── Cancel ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "apply_cancel")
async def cb_apply_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("تم الإلغاء.")
    await callback.message.edit_text("❌ <b>تم إلغاء عملية التقديم.</b>", parse_mode="HTML", reply_markup=None)


# ── Inline pause/resume ───────────────────────────────────────────────────────

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
