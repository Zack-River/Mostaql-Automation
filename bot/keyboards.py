"""
bot/keyboards.py — Inline keyboard builders for Telegram messages.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def job_keyboard(job_url: str, job_id: str) -> InlineKeyboardMarkup:
    """Initial notification keyboard on a new job."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="👁  عرض المشروع على مستقل", url=job_url)
    )
    builder.row(
        InlineKeyboardButton(text="🤖  توليد عرض مخصص", callback_data=f"gen_proposal:{job_id}"),
        InlineKeyboardButton(text="📊  تقييم المشروع", callback_data=f"rate_project:{job_id}"),
    )
    return builder.as_markup()


def rating_result_keyboard(job_id: str) -> InlineKeyboardMarkup:
    """Keyboard attached to the rating output."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="🤖  توليد العرض الآن", callback_data=f"gen_proposal:{job_id}")
    )
    return builder.as_markup()


def proposal_keyboard(job_id: str) -> InlineKeyboardMarkup:
    """Keyboard attached to the generated proposal — quick apply + custom apply + rewrite."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⚡  تقديم سريع (AI)", callback_data=f"quick_apply:{job_id}"),
        InlineKeyboardButton(text="🛠  تقديم مخصص", callback_data=f"custom_apply:{job_id}"),
    )
    builder.row(
        InlineKeyboardButton(text="✏️  تعديل العرض بملاحظة", callback_data=f"rewrite_proposal:{job_id}")
    )
    return builder.as_markup()


# ── Custom Apply Step Keyboards ───────────────────────────────────────────────

def price_step_keyboard(job_id: str, default_price: str) -> InlineKeyboardMarkup:
    """Ask for price with optional default suggestion."""
    builder = InlineKeyboardBuilder()
    if default_price:
        builder.row(
            InlineKeyboardButton(
                text=f"✅ قبول المقترح: {default_price}$",
                callback_data=f"use_default:price:{job_id}",
            )
        )
    builder.row(
        InlineKeyboardButton(text="❌  إلغاء", callback_data="apply_cancel")
    )
    return builder.as_markup()


def duration_step_keyboard(job_id: str, default_duration: str) -> InlineKeyboardMarkup:
    """Ask for duration with optional default suggestion."""
    builder = InlineKeyboardBuilder()
    if default_duration:
        builder.row(
            InlineKeyboardButton(
                text=f"✅ قبول المقترح: {default_duration} يوم",
                callback_data=f"use_default:duration:{job_id}",
            )
        )
    builder.row(
        InlineKeyboardButton(text="❌  إلغاء", callback_data="apply_cancel")
    )
    return builder.as_markup()


def question_step_keyboard(job_id: str, q_index: int, default_answer: str) -> InlineKeyboardMarkup:
    """Ask for question answer with optional AI-suggested default."""
    builder = InlineKeyboardBuilder()
    if default_answer:
        display = (default_answer[:45] + "...") if len(default_answer) > 45 else default_answer
        builder.row(
            InlineKeyboardButton(
                text=f"✅ قبول المقترح",
                callback_data=f"use_default:question:{q_index}:{job_id}",
            )
        )
    builder.row(
        InlineKeyboardButton(text="❌  إلغاء", callback_data="apply_cancel")
    )
    return builder.as_markup()


def apply_confirm_keyboard(job_id: str) -> InlineKeyboardMarkup:
    """Final confirmation keyboard before submitting."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="✅  تأكيد وإرسال", callback_data=f"apply_confirm:{job_id}"),
        InlineKeyboardButton(text="❌  إلغاء", callback_data="apply_cancel"),
    )
    return builder.as_markup()


def status_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for the /status command."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="⏸  إيقاف مؤقت", callback_data="pause"),
        InlineKeyboardButton(text="▶️  استئناف", callback_data="resume"),
    )
    return builder.as_markup()
