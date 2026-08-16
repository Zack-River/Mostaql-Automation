"""
bot/states.py — FSM states for the proposal apply flows.

Quick Apply Flow:
  quick_confirm   → show summary (AI-filled), waiting for confirm/cancel

Custom Apply Flow:
  custom_price    → waiting for user to enter price (shows AI default)
  custom_duration → waiting for user to enter duration
  custom_question → waiting for user to answer a custom question
  custom_confirm  → show summary, waiting for confirm/cancel

Rewrite Flow:
  rewrite_note    → waiting for user to send note to modify proposal
"""
from aiogram.fsm.state import State, StatesGroup


class ApplyFlow(StatesGroup):
    # Proposal rewrite
    rewrite_note = State()

    # Quick apply
    quick_confirm = State()

    # Custom apply
    custom_price    = State()
    custom_duration = State()
    custom_question = State()
    custom_confirm  = State()
