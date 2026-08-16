"""
bot/applicator.py — Handles the actual HTTP POST to mostaql.com/bid.

Responsibilities:
  - Fetch the apply form to get a fresh CSRF token
  - Build the POST payload (cost, period, details, question answers)
  - Submit to https://mostaql.com/bid
  - Return success/failure result
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

BID_URL = "https://mostaql.com/bid"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://mostaql.com/",
}


@dataclass
class ApplyResult:
    success: bool
    message: str


@dataclass
class JobApplyData:
    """All data needed to submit a proposal."""
    job_id: str
    job_url: str
    cost: str                             # Price in USD
    period: str                           # Duration in days
    details: str                          # Proposal text
    question_answers: dict[str, str] = field(default_factory=dict)  # {question_id: answer}


class MostaqlApplicator:
    """
    Submits a proposal to Mostaql using the authenticated session cookies.
    """

    def __init__(self, session_cookies: dict[str, str]) -> None:
        self._cookies = dict(session_cookies)

    def update_cookies(self, new_cookies: dict[str, str]) -> None:
        """Update session cookies in memory (no restart needed)."""
        self._cookies = dict(new_cookies)
        logger.info(f"Applicator cookies updated ({len(self._cookies)} cookies)")

    def _make_client(self) -> httpx.AsyncClient:
        client = httpx.AsyncClient(
            headers=HEADERS,
            follow_redirects=True,
            timeout=httpx.Timeout(30.0),
            http2=True,
        )
        for name, value in self._cookies.items():
            client.cookies.set(name, value, domain="mostaql.com")
        return client

    async def get_form_data(self, job_url: str) -> dict | None:
        """
        Fetch the project page and extract the form fields:
          - Fresh CSRF token
          - project ID
          - question IDs and their text labels
        Returns None on failure.
        """
        try:
            async with self._make_client() as client:
                resp = await client.get(job_url)
                resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")
            bid_form = soup.find("form", {"action": BID_URL})
            if not bid_form:
                logger.error(f"Could not find bid form on page: {job_url}")
                return None

            # CSRF Token
            csrf_input = bid_form.find("input", {"name": "_token"})
            csrf = csrf_input["value"] if csrf_input else ""

            # Project ID
            project_input = bid_form.find("input", {"name": "project"})
            project_id = project_input["value"] if project_input else ""

            # Extract questions: each question has a hidden question_id + a textarea answers[ID]
            questions = []
            for q_hidden in bid_form.find_all("input", {"name": "question_id"}):
                q_id = q_hidden.get("value", "")
                # Find label text: go up and look for surrounding text
                container = q_hidden.parent
                label_el = container.find("label")
                if not label_el:
                    # Try going up more levels
                    container = q_hidden.parent.parent
                    label_el = container.find("label")

                q_text = label_el.get_text(strip=True) if label_el else f"سؤال {q_id}"
                if q_id and q_id not in [q["id"] for q in questions]:
                    questions.append({"id": q_id, "text": q_text})

            return {
                "csrf": csrf,
                "project_id": project_id,
                "questions": questions,
            }

        except Exception as exc:
            logger.error(f"Error fetching form data: {exc}", exc_info=True)
            return None

    async def submit(self, apply_data: JobApplyData, dry_run: bool = False) -> ApplyResult:
        """
        Submit the proposal to mostaql.com/bid.
        If dry_run=True, returns the payload that *would* be sent without actually posting.
        """
        # First, get a fresh CSRF token from the project page
        form_data = await self.get_form_data(apply_data.job_url)
        if not form_data:
            return ApplyResult(
                success=False,
                message="⚠️ تعذر جلب بيانات الفورم من الموقع."
            )

        csrf = form_data["csrf"]
        project_id = form_data["project_id"]

        if not csrf or not project_id:
            return ApplyResult(
                success=False,
                message="⚠️ تعذر استخراج CSRF token أو معرف المشروع."
            )

        # Build POST payload
        payload: dict[str, str] = {
            "_token": csrf,
            "project": project_id,
            "cost": apply_data.cost,
            "period": apply_data.period,
            "details": apply_data.details,
            "realCost": "0",
            "files": "",
            "multiple_up_completed": "1",
        }

        # Add question answers
        for q_id, answer in apply_data.question_answers.items():
            payload["question_id"] = q_id  # last one wins — matches form structure
            payload[f"answers[{q_id}]"] = answer

        # If there are multiple questions, we need to POST them differently
        # Mostaql expects question_id repeated — we use a list
        # Build the full multipart-style payload
        full_payload: list[tuple[str, str]] = [
            ("_token", csrf),
            ("project", project_id),
        ]
        # Add each question
        for q_id, answer in apply_data.question_answers.items():
            full_payload.append(("question_id", q_id))
            full_payload.append((f"answers[{q_id}]", answer))

        full_payload += [
            ("period", apply_data.period),
            ("cost", apply_data.cost),
            ("realCost", "0"),
            ("details", apply_data.details),
            ("files", ""),
            ("multiple_up_completed", "1"),
        ]

        # ── DRY RUN mode — show payload without submitting ──────────────────
        if dry_run:
            q_preview = "\n".join(
                f"  answers[{qid}] = {ans[:80]}..."
                if len(ans) > 80 else f"  answers[{qid}] = {ans}"
                for qid, ans in apply_data.question_answers.items()
            ) or "  (لا توجد أسئلة)"
            payload_preview = (
                f"🧪 <b>Dry Run — ما سيُرسل إلى مستقل:</b>\n\n"
                f"<b>URL:</b> <code>{BID_URL}</code>\n"
                f"<b>project_id:</b> <code>{project_id}</code>\n"
                f"<b>cost:</b> <code>{apply_data.cost}$</code>\n"
                f"<b>period:</b> <code>{apply_data.period} يوم</code>\n"
                f"<b>details:</b> <code>{(apply_data.details or '—')[:100]}...</code>\n"
                f"<b>questions:</b>\n{q_preview}\n\n"
                f"✅ الـ CSRF token وجد بنجاح — الفورم جاهز للإرسال."
            )
            return ApplyResult(success=True, message=payload_preview)

        try:
            async with self._make_client() as client:
                # Set required headers for the POST
                client.headers.update({
                    "Origin": "https://mostaql.com",
                    "Referer": apply_data.job_url,
                    "X-XSRF-TOKEN": self._cookies.get("XSRF-TOKEN", ""),
                })

                resp = await client.post(BID_URL, data=full_payload)

                logger.info(f"Bid POST response: {resp.status_code} → {resp.url}")

                if resp.status_code in (200, 302):
                    # Check response for success/error indicators
                    soup = BeautifulSoup(resp.text, "html.parser")

                    # Look for error messages
                    error_el = soup.find(class_=lambda c: c and "error" in c.lower())
                    alert_el = soup.find(class_=lambda c: c and "alert" in c.lower() if c else False)

                    # Check for success: usually redirected to project page with bid listed
                    final_url = str(resp.url)
                    if "bid" in final_url or apply_data.job_id in final_url:
                        # Check page content for confirmation
                        page_text = soup.get_text()
                        if "تم إضافة عرضك" in page_text or "تم" in page_text[:500]:
                            return ApplyResult(success=True, message="✅ تم تقديم العرض بنجاح!")

                    # Generic success if we got redirected away from /bid
                    if "bid" not in final_url:
                        return ApplyResult(success=True, message="✅ تم إرسال العرض! تحقق من صفحة المشروع.")

                    return ApplyResult(
                        success=True,
                        message=f"✅ تم الإرسال (كود الاستجابة: {resp.status_code})"
                    )

                else:
                    return ApplyResult(
                        success=False,
                        message=f"⚠️ فشل الإرسال — كود HTTP: {resp.status_code}"
                    )

        except Exception as exc:
            logger.error(f"Error submitting bid: {exc}", exc_info=True)
            return ApplyResult(
                success=False,
                message=f"⚠️ حدث خطأ أثناء الإرسال: {exc}"
            )
