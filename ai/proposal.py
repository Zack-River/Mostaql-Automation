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
                            temperature=0.45,   # Lower than before (was 0.7) to reduce hallucination
                            max_output_tokens=2500,  # Concise proposals; was 8000
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
                job.rating_text = text
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
            if hasattr(job, 'rating_text') and job.rating_text:
                fmt_kwargs["user_notes"] += f"\n\nالتقييم الداخلي (Internal Rating):\n{job.rating_text}"

        return template.format(**fmt_kwargs)

    async def suggest_apply_params(
        self,
        job: Job,
        form_questions: list[dict],
    ) -> dict:
        """
        Returns AI-suggested bid parameters as a dict:
          {
            "price": "150",
            "duration": "10",
            "question_answers": [
              {"index": 0, "id": "<q_id>", "answer": "..."},
              ...
            ]
          }
        Returns {} on failure.
        """
        if not self._enabled:
            return {}

        q_lines = ""
        for i, q in enumerate(form_questions):
            q_lines += f"{i + 1}. {q.get('text', '')}\n"

        prompt = f"""أنت خبير في تقديم العروض على منصة مستقل واستراتيجي تسعير (Pricing Strategist).

بناءً على تفاصيل المشروع أدناه، أعطني:
1. السعر المناسب للتقديم بالدولار (رقم واحد محدد، ليس نطاقاً). **هام جداً:** يجب أن يكون السعر ضمن نطاق "الميزانية المعلنة" حرفياً، لا يمكن وضع سعر أعلى من الحد الأقصى للميزانية لأن منصة مستقل سترفض الإرسال.
2. مدة التسليم المناسبة للتقديم بالأيام (رقم واحد).
3. إجابات مهنية ومقنعة على أسئلة المشروع إن وجدت.

تفاصيل المشروع:
العنوان: {job.title}
الوصف: {(job.full_description or job.description_snippet or "")[:1500]}
الميزانية المعلنة: {job.budget or "غير محدد"}
المدة المطلوبة: {job.duration or "غير محدد"}

الأسئلة الإضافية ({len(form_questions)}):
{q_lines or "لا توجد أسئلة"}

التقييم الداخلي السابق (استخدم هذا التقييم لتحديد السعر والمدة المقترحة):
{job.rating_text or "لا يوجد تقييم مسبق"}

# PRICING & DURATION MENTAL MODEL (STRICT RULES)
استخدم نفس معايير التقييم لحساب السعر والمدة:
1. **Realistic Effort (الجهد الواقعي)** = حجم العمل الفعلي + التعقيد + العمل المخفي (Hidden Work) + أوقات الاختبار (QA) + المخاطر.
2. احسب داخلياً (Fair Price) و (Realistic Duration) بناءً على الجهد الواقعي.
3. افصل تماماً بين "مدة العميل" و"المدة الواقعية المقترحة". إذا كانت مدة العميل (مثلاً 7 أيام) مستحيلة لتنفيذ تطبيق كامل، يجب أن تقترح المدة الواقعية (مثلاً 30 يوماً).
4. افصل تماماً بين "ميزانية العميل" و"السعر العادل". لا ترفع السعر لمجرد أن الميزانية عالية.
5. **SKIP CONSISTENCY**: إذا كان السعر العادل (Fair Price) أعلى بكثير من ميزانية العميل (مثلاً 6000$ والميزانية 2500$)، لا تخفض سعر التقديم إلى 2500$ لتفوز بالمشروع. يجب أن تقترح السعر الواقعي (6000$) وتتجاهل ميزانية العميل، لأن هذا المشروع يُفترض أن يكون (SKIP). لا تعطِ سعراً يجعلك تعمل بخسارة أبداً.
6. لا تفترض وجود تقنيات (Dashboards, APIs, Payment, Backend) إذا لم تذكر، ولكن قدّر المخاطر المرتبطة بالغموض عند التسعير.
7. إذا كانت الأسئلة تطلب إجابات عن خبرة غير موجودة، لا تخترعها. قدم إجابات تعكس فهمك الفعلي كـ Software Engineer & QA.

أجب بـ JSON صالح (Valid JSON) فقط، بدون أي نص قبله أو بعده، وبدون أي علامات Markdown:
{{
  "price": <number>,
  "duration": <number>,
  "question_answers": [
    {{"index": 0, "answer": "<إجابة السؤال الأول>"}},
    {{"index": 1, "answer": "<إجابة السؤال الثاني>"}}
  ]
}}
إذا لم توجد أسئلة، اترك question_answers قائمة فارغة [].
"""


        for i, client in enumerate(self._clients):
            try:
                import json, re as _re
                loop = asyncio.get_event_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda c=client: c.models.generate_content(
                        model=self._model_name,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            temperature=0.3,
                            max_output_tokens=2000,
                        ),
                    ),
                )
                text = response.text.strip() if response.text else ""
                # Extract JSON block
                match = _re.search(r'\{.*\}', text, _re.DOTALL)
                if match:
                    data = json.loads(match.group())
                    # Attach q_id to each answer using index
                    for ans in data.get("question_answers", []):
                        idx = ans.get("index", -1)
                        if 0 <= idx < len(form_questions):
                            ans["id"] = form_questions[idx].get("id", "")
                    logger.info(f"suggest_apply_params OK for job {job.id}: {data}")
                    return data
            except Exception as exc:
                is_429 = "429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc)
                if is_429 and i < len(self._clients) - 1:
                    logger.warning(f"Rate limit on key {i}, falling back...")
                    continue
                logger.error(f"suggest_apply_params error: {exc}")
                return {}

        return {}

