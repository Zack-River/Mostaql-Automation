"""
bot/keyboards.py — Inline keyboard builders for Telegram messages.
"""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def job_keyboard(job_url: str, job_id: str) -> InlineKeyboardMarkup:
    """
    Returns an inline keyboard with:
      Row 1: [👁 عرض المشروع]  ← opens the job URL in browser
      Row 2: [📋 نسخ العرض]   ← triggers callback to send proposal as copyable text
    """
    builder = InlineKeyboardBuilder()

    builder.row(
        InlineKeyboardButton(
            text="👁  عرض المشروع على مستقل",
            url=job_url,
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="🤖  توليد عرض مخصص",
            callback_data=f"gen_proposal:{job_id}",
        )
    )
    builder.row(
        InlineKeyboardButton(
            text="📊  تقييم المشروع",
            callback_data=f"rate_project:{job_id}",
        )
    )

    return builder.as_markup()


def rating_result_keyboard(job_id: str) -> InlineKeyboardMarkup:
    """Keyboard attached to the rating output, allows generating a proposal directly."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="🤖  توليد العرض الآن",
            callback_data=f"gen_proposal:{job_id}",
        )
    )
    return builder.as_markup()


def proposal_keyboard(job_id: str) -> InlineKeyboardMarkup:
    """Keyboard attached to the generated proposal — adds an Apply button."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅  تقديم العرض الآن",
            callback_data=f"apply_start:{job_id}",
        )
    )
    return builder.as_markup()


def apply_confirm_keyboard(job_id: str) -> InlineKeyboardMarkup:
    """Final confirmation keyboard before submitting the proposal."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅  تأكيد وإرسال",
            callback_data=f"apply_confirm:{job_id}",
        ),
        InlineKeyboardButton(
            text="❌  إلغاء",
            callback_data="apply_cancel",
        ),
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
