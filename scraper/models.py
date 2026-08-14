"""
scraper/models.py — Data models for Mostaql jobs.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ClientProfile:
    """Stats about the client who posted the job (from sidebar)."""
    name: str = ""
    registration_date: str = ""
    hiring_rate: str = ""          # e.g. "79.17%"
    hiring_rate_label: str = ""    # e.g. "excellent" | "good" | "average"
    open_projects: str = ""        # e.g. "1"
    in_progress_projects: str = "" # e.g. "0"
    ongoing_communications: str = "" # e.g. "2"
    profile_url: str = ""


@dataclass
class JobQuestion:
    """A single pre-application question on the job page."""
    question: str
    ai_answer: str = ""            # Filled by GeminiProposalGenerator


@dataclass
class Job:
    """Full representation of a Mostaql project."""
    # ── Core identity ────────────────────────────────────────────────
    id: str = ""
    title: str = ""
    url: str = ""

    # ── From listing page (always available) ─────────────────────────
    description_snippet: str = ""
    poster_name: str = ""
    time_posted: str = ""          # e.g. "منذ ساعة"
    proposals_count: str = ""      # e.g. "6 عروض" | "أضف أول عرض"

    # ── From detail page (available without login) ────────────────────
    full_description: str = ""
    budget: str = ""               # e.g. "$25.00 - $50.00"
    duration: str = ""             # e.g. "3 أيام"
    publish_date: str = ""         # e.g. "منذ ساعة" (same as time_posted but from detail)
    status: str = ""               # e.g. "مفتوح"
    skills: list[str] = field(default_factory=list)

    # ── Client profile (from sidebar, needs login for full data) ─────
    client: ClientProfile = field(default_factory=ClientProfile)

    # ── Pre-application questions (login required) ────────────────────
    questions: list[JobQuestion] = field(default_factory=list)

    # ── AI-generated content ──────────────────────────────────────────
    proposal: str = ""

    @property
    def has_questions(self) -> bool:
        return len(self.questions) > 0

    @property
    def skills_str(self) -> str:
        return "، ".join(self.skills) if self.skills else "—"

    def __str__(self) -> str:
        return f"Job({self.id}: {self.title[:40]})"
