"""
bot/states.py — FSM states for the proposal apply flow.

Flow:
  APPLY_PRICE     → waiting for user to send price
  APPLY_DURATION  → waiting for user to send duration
  APPLY_QUESTION  → waiting for answer to a custom job question
  APPLY_CONFIRM   → showing summary, waiting for confirm/cancel
"""
from aiogram.fsm.state import State, StatesGroup


class ApplyFlow(StatesGroup):
    waiting_price    = State()
    waiting_duration = State()
    waiting_question = State()
    waiting_confirm  = State()
