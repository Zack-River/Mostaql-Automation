"""
ai/proposal.py — Gemini-powered proposal generator.

Uses the current `google-genai` SDK (the new official SDK, replacing
the deprecated `google-generativeai` package).

Responsibilities:
  - Build a rich prompt from the base persona + full job details.
  - Answer any pre-application questions found on the job page.
  - Return a complete, ready-to-send proposal string.

Model: gemini-2.0-flash
  - Chosen for: fast response, great Arabic quality, large context window,
    generous free tier.
"""
from __future__ import annotations

import asyncio
import logging
import warnings

# Suppress the AFC FutureWarning from the google-genai SDK
warnings.filterwarnings("ignore", category=FutureWarning, module="google")

from google import genai
from google.genai import types

from ai.profile import RAWAN_PROFILE
from ai.prompts import MOSTAQL_SYSTEM_PROMPT, MOSTAQL_RATING_PROMPT
from scraper.models import Job, JobQuestion

logger = logging.getLogger(__name__)


class GeminiProposalGenerator:
    """
    Generates freelance proposals using the Gemini API.

    Usage:
        generator = GeminiProposalGenerator(api_key, model, base_prompt)
        proposal  = await generator.generate(job)
    """

    def __init__(
        self,
        api_keys: list[str],
        model_name: str,
        base_proposal_prompt: str,
    ) -> None:
        self._keys = [k for k in api_keys if k]
        if not self._keys:
            logger.warning(
                "No GEMINI_API_KEY provided. Proposal generation will be disabled."
            )
            self._enabled = False
            return

        self._clients = [genai.Client(api_key=k) for k in self._keys]
        self._model_name = model_name
        self._base_prompt = base_proposal_prompt.strip()
        self._enabled = True
        logger.info(f"GeminiProposalGenerator ready with {len(self._clients)} key(s) (model={model_name})")

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    # ── Public API ─────────────────────────────────────────────────────────────
    async def generate(self, job: Job, user_notes: str = "") -> str:
        """
        Generate a complete proposal for the given job.
        Returns the proposal text, or an empty string if AI is disabled.
        """
        if not self._enabled:
            return ""

        prompt = self._build_prompt(job, user_notes=user_notes)
        
        for i, client in enumerate(self._clients):
            try:
                # google-genai is sync; run in executor to keep event loop free
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda c=client: c.models.generate_content(
                        model=self._model_name,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            temperature=0.7,
                            max_output_tokens=8000,
                        ),
                    ),
                )
                text = response.text.strip() if response.text else ""
                logger.info(f"Proposal generated for job {job.id} using key index {i} ({len(text)} chars)")
                return text
            except Exception as exc:
                is_429 = "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc)
                if is_429 and i < len(self._clients) - 1:
                    logger.warning(f"Gemini API rate limit on key {i}. Falling back to next key...")
                    continue
                logger.error(f"Gemini API error for job {job.id} (key {i}): {exc}")
                return ""

    async def rate_job(self, job: Job) -> str:
        """
        Generate a rating for the given job.
        Returns the rating text, or an empty string if AI is disabled.
        """
        if not self._enabled:
            return ""

        prompt = self._build_prompt(job, is_rating=True)
        
        for i, client in enumerate(self._clients):
            try:
                # google-genai is sync; run in executor to keep event loop free
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda c=client: c.models.generate_content(
                        model=self._model_name,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            temperature=0.4,
                            max_output_tokens=8000,
                        ),
                    ),
                )
                text = response.text.strip() if response.text else ""
                logger.info(f"Rating generated for job {job.id} using key index {i} ({len(text)} chars)")
                return text
            except Exception as exc:
                is_429 = "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc)
                if is_429 and i < len(self._clients) - 1:
                    logger.warning(f"Gemini API rate limit on key {i}. Falling back to next key...")
                    continue
                logger.error(f"Gemini API error (rating) for job {job.id} (key {i}): {exc}")
                return ""

    def _build_prompt(self, job: Job, is_rating: bool = False, user_notes: str = "") -> str:
        """
        Builds the prompt using the user's custom system prompt template.
        """
        client_info = []
        c = job.client
        if c.name:
            client_info.append(f"الاسم: {c.name}")
        if c.hiring_rate:
            client_info.append(f"معدل التوظيف: {c.hiring_rate}")
        if c.open_projects:
            client_info.append(f"المشاريع المفتوحة: {c.open_projects}")
        if c.in_progress_projects:
            client_info.append(f"مشاريع قيد التنفيذ: {c.in_progress_projects}")
        if c.ongoing_communications:
            client_info.append(f"التواصلات الجارية: {c.ongoing_communications}")
        if c.registration_date:
            client_info.append(f"تاريخ التسجيل: {c.registration_date}")

        client_info_str = "\n".join(client_info) if client_info else "غير محدد"

        questions_text = "لا توجد أسئلة."
        if job.has_questions:
            questions_text = "\n".join(q.question for q in job.questions) if job.questions else "لا توجد أسئلة."

        template = MOSTAQL_RATING_PROMPT if is_rating else MOSTAQL_SYSTEM_PROMPT

        fmt_kwargs = dict(
            project_description=job.full_description or job.description_snippet or "غير محدد",
            project_budget=job.budget or "غير محدد",
            project_duration=job.duration or "غير محدد",
            client_information=client_info_str,
            questions=questions_text,
            developer_experience=RAWAN_PROFILE,
        )
        # user_notes only exists in MOSTAQL_SYSTEM_PROMPT template
        if not is_rating:
            fmt_kwargs["user_notes"] = user_notes or "لا توجد ملاحظات إضافية."

        return template.format(**fmt_kwargs)
