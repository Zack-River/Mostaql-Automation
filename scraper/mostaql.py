"""
scraper/mostaql.py — Async scraper for mostaql.com

Architecture:
  - One shared httpx.AsyncClient (connection pooling, keep-alive).
  - Supports two modes:
      PUBLIC: no login, scrapes all publicly visible fields.
      AUTHENTICATED: logs in first, then also scrapes hiring-rate,
                     open/in-progress counts, and pre-application questions.
  - All parsing is done with BeautifulSoup4 + lxml.

HTML structure confirmed via live browser inspection (Aug 2026):
  Listing page   → https://mostaql.com/projects?category_id=1&sort=date
  Detail page    → https://mostaql.com/project/{id}-{slug}
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup, Tag

from scraper.models import ClientProfile, Job, JobQuestion

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
BASE_URL = "https://mostaql.com"
LOGIN_URL = f"{BASE_URL}/login"

# Realistic browser User-Agent to avoid bot-detection 429/403 responses
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ar,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _text(tag: Optional[Tag], default: str = "—") -> str:
    """Safe inner-text extraction from a BeautifulSoup Tag."""
    if tag is None:
        return default
    return tag.get_text(strip=True) or default


def _extract_job_id(href: str) -> str:
    """
    Extract numeric project ID from hrefs like:
      /project/1266021-تصميم-3d
    Returns '1266021'.
    """
    match = re.search(r"/project/(\d+)", href)
    return match.group(1) if match else ""


# ── Main Scraper Class ──────────────────────────────────────────────────────────
class MostaqlScraper:
    """
    Async scraper for mostaql.com.

    Usage:
        async with MostaqlScraper(settings) as scraper:
            if scraper.is_authenticated:
                await scraper.login()
            jobs = await scraper.fetch_jobs_list()
            for job in jobs:
                await scraper.fetch_job_details(job)
    """

    def __init__(
        self,
        jobs_url: str,
        mostaql_email: str = "",
        mostaql_password: str = "",
        session_cookies: dict | None = None,
    ) -> None:
        self.jobs_url = jobs_url
        self._email = mostaql_email
        self._password = mostaql_password
        self._logged_in = False

        # Single client with cookie jar — persists session across requests
        self._client = httpx.AsyncClient(
            headers=HEADERS,
            follow_redirects=True,
            timeout=httpx.Timeout(20.0),
            http2=True,
        )

        # Inject pre-loaded session cookies if provided (skips login flow)
        if session_cookies:
            self._inject_cookies(session_cookies)
            self._logged_in = True
            logger.info("Session cookies loaded — skipping login flow.")

    def _inject_cookies(self, cookies: dict) -> None:
        """Inject cookies directly into the httpx client cookie jar."""
        for name, value in cookies.items():
            self._client.cookies.set(name, value, domain="mostaql.com")
        logger.debug(f"Injected {len(cookies)} session cookies.")

    # ── Context manager ────────────────────────────────────────────────────────
    async def __aenter__(self) -> "MostaqlScraper":
        return self

    async def __aexit__(self, *_) -> None:
        await self._client.aclose()

    @property
    def is_authenticated(self) -> bool:
        return bool(self._email and self._password)

    # ── Authentication ─────────────────────────────────────────────────────────
    async def login(self) -> str | bool:
        """
        Log in to Mostaql using email/password.
        Returns:
          True          — login succeeded without 2FA
          "2FA_REQUIRED" — credentials OK but 2FA code needed
          False         — login failed
        """
        if not self.is_authenticated:
            logger.warning("Login skipped — no Mostaql credentials configured.")
            return False

        try:
            # Step 1: GET login page to obtain CSRF token
            resp = await self._client.get(LOGIN_URL)
            resp.raise_for_status()
            soup = _soup(resp.text)

            csrf_input = soup.find("input", {"name": "_token"})
            if not csrf_input:
                csrf_meta = soup.find("meta", {"name": "csrf-token"})
                csrf = csrf_meta["content"] if csrf_meta else ""
            else:
                csrf = csrf_input.get("value", "")

            if not csrf:
                logger.error("Could not find CSRF token on login page.")
                return False

            # Step 2: POST credentials
            payload = {
                "_token": csrf,
                "email": self._email,
                "password": self._password,
                "remember": "1",
            }
            login_resp = await self._client.post(LOGIN_URL, data=payload)
            login_resp.raise_for_status()

            final_url = str(login_resp.url)
            page_text  = login_resp.text

            # Detect 2FA page
            if "two-factor" in final_url or "two_factor" in final_url or "verification" in final_url:
                logger.info("2FA required — waiting for user code")
                return "2FA_REQUIRED"

            # Also detect 2FA by page content
            if "two-factor" in page_text.lower() or "رمز التحقق" in page_text:
                logger.info("2FA required (detected in page content)")
                return "2FA_REQUIRED"

            # Check successful login
            if "logout" in page_text.lower() or "تسجيل الخروج" in page_text:
                self._logged_in = True
                logger.info("✅ Logged in to Mostaql successfully (no 2FA).")
                return True

            logger.error("Login POST returned 200 but session not established. Check credentials.")
            return False

        except httpx.HTTPError as exc:
            logger.error(f"HTTP error during Mostaql login: {exc}")
            return False

    async def submit_2fa(self, code: str) -> bool:
        """
        Submit a 2FA verification code to complete the login flow.
        Call this after login() returns "2FA_REQUIRED".
        Returns True on success.
        """
        TWOFA_URL = f"{BASE_URL}/two-factor-challenge"
        try:
            # GET the 2FA page for a fresh CSRF token
            resp = await self._client.get(TWOFA_URL)
            soup = _soup(resp.text)

            csrf_input = soup.find("input", {"name": "_token"})
            csrf = csrf_input.get("value", "") if csrf_input else ""
            if not csrf:
                csrf_meta = soup.find("meta", {"name": "csrf-token"})
                csrf = csrf_meta["content"] if csrf_meta else ""

            payload = {"_token": csrf, "code": code}
            twofa_resp = await self._client.post(TWOFA_URL, data=payload)

            page_text = twofa_resp.text
            if "logout" in page_text.lower() or "تسجيل الخروج" in page_text:
                self._logged_in = True
                logger.info("✅ 2FA verification successful — fully logged in.")
                return True

            logger.error("2FA code rejected or session not established.")
            return False

        except httpx.HTTPError as exc:
            logger.error(f"HTTP error during 2FA submission: {exc}")
            return False

    def extract_session_cookies(self) -> dict[str, str]:
        """Extract current mostaql session cookies from the httpx client."""
        names = ["AWSALB", "AWSALBCORS", "mostaqlweb", "XSRF-TOKEN"]
        result: dict[str, str] = {}
        for name in names:
            val = self._client.cookies.get(name)
            if not val:
                for cookie in self._client.cookies.jar:
                    if cookie.name == name:
                        val = cookie.value
                        break
            if val:
                result[name] = val
        return result

    # ── Listing Page ───────────────────────────────────────────────────────────
    async def fetch_jobs_list(self) -> list[Job]:
        """
        Fetch the first page of programming jobs.
        Returns a list of Job objects with basic info (id, title, url, snippet).
        Full details are populated by fetch_job_details().
        """
        try:
            resp = await self._client.get(self.jobs_url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error(f"Failed to fetch jobs list: {exc}")
            return []

        return self._parse_jobs_list(resp.text)

    def _parse_jobs_list(self, html: str) -> list[Job]:
        soup = _soup(html)
        jobs: list[Job] = []

        # Each project is inside a <tr> or a wrapper with a project link
        # Confirmed structure: title is an <a> with href="/project/{id}-{slug}"
        # We search for all project links to find cards
        project_links = soup.find_all("a", href=re.compile(r"/project/\d+"))

        seen_ids: set[str] = set()  # avoid duplicates from multiple links per card
        for link in project_links:
            href = link.get("href", "")
            job_id = _extract_job_id(href)

            if not job_id or job_id in seen_ids:
                continue
            seen_ids.add(job_id)

            title = _text(link)
            if not title or len(title) < 3:
                continue  # skip nav links etc.

            full_url = BASE_URL + href if href.startswith("/") else href

            # Walk up to find the card container
            card = link.find_parent("tr") or link.find_parent(
                "div", class_=re.compile(r"project|card|item")
            )

            snippet = ""
            poster = ""
            time_posted = ""
            proposals = ""

            if card:
                # Description snippet — usually the first long <a> or <p> after title
                details_link = card.find("a", class_=re.compile(r"details|desc"))
                if details_link:
                    snippet = _text(details_link)
                elif card.find("p"):
                    snippet = _text(card.find("p"))

                # Poster name (inside <bdi> within author link)
                bdi_tags = card.find_all("bdi")
                if bdi_tags:
                    poster = _text(bdi_tags[0])

                # Time posted — text containing "منذ" or "ساعة" or "يوم"
                for text_el in card.find_all(string=re.compile(r"منذ|ساعة|يوم|دقيقة")):
                    time_posted = text_el.strip()
                    break

                # Proposals count
                for text_el in card.find_all(
                    string=re.compile(r"عرض|عروض|أضف أول")
                ):
                    proposals = text_el.strip()
                    break

            jobs.append(
                Job(
                    id=job_id,
                    title=title,
                    url=full_url,
                    description_snippet=snippet,
                    poster_name=poster,
                    time_posted=time_posted,
                    proposals_count=proposals,
                )
            )

        logger.info(f"Parsed {len(jobs)} jobs from listing page.")
        return jobs

    # ── Detail Page ────────────────────────────────────────────────────────────
    async def fetch_job_details(self, job: Job) -> None:
        """
        Fetch the detail page for a job and populate all remaining fields
        directly on the passed Job object (mutates in place).

        Fields populated:
          - full_description, budget, duration, publish_date, status, skills
          - client.* (name, hiring_rate, open_projects, etc.)
          - questions (if logged in and questions exist)
        """
        try:
            resp = await self._client.get(job.url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error(f"Failed to fetch details for job {job.id}: {exc}")
            return

        soup = _soup(resp.text)

        # ── Full description ───────────────────────────────────────────────────
        # Confirmed structure: <div class="text-wrapper-div carda__content">
        desc_div = soup.find("div", class_="carda__content")
        if desc_div:
            job.full_description = desc_div.get_text(separator="\n", strip=True).strip()

        # ── Sidebar card parsing ───────────────────────────────────────────────
        # The right sidebar contains two cards:
        #   Card 1 → project metadata (status, date, budget, duration, skills)
        #   Card 2 → client profile (owner name, hiring rate, open projects, etc.)
        self._parse_sidebar(soup, job)

        # ── Pre-application questions (authenticated only) ─────────────────────
        if self._logged_in:
            self._parse_questions(soup, job)

    def _parse_sidebar(self, soup: BeautifulSoup, job: Job) -> None:
        """
        Parse both sidebar cards and populate job fields.

        Confirmed HTML structure (inspected Aug 2026 on live page):
          Project metadata  → <div class="meta-row">
                                <div class="meta-label">الميزانية</div>
                                <div class="meta-value">$25.00 - $50.00</div>
                              </div>
          Skills            → <a class="tag"><i class="fa fa-tag"></i><bdi>name</bdi></a>
          Status badge      → <bdi class="label label-prj-open">مفتوح</bdi>
          Hiring rate badge → <label class="label label-rating-excellent">79.17%</label>
          Client stats      → <td><span ...>المشاريع المفتوحة</span></td>
                              <td>1</td>  (next sibling td)
        """
        # ── Project metadata via meta-row/meta-label/meta-value ───────────────
        meta: dict[str, str] = {}
        for row in soup.find_all("div", class_="meta-row"):
            label_el = row.find("div", class_="meta-label")
            value_el = row.find("div", class_="meta-value")
            if label_el and value_el:
                key = label_el.get_text(strip=True)
                val = " ".join(value_el.get_text(separator=" ", strip=True).split())
                meta[key] = val

        job.budget       = meta.get("الميزانية", "")
        job.duration     = meta.get("مدة التنفيذ", "")
        job.status       = meta.get("حالة المشروع", "")
        job.publish_date = meta.get("تاريخ النشر", job.time_posted)

        # ── Skills — use <bdi> text to avoid duplicating icon text ────────────
        skill_tags = soup.find_all("a", class_="tag")
        seen_skills: set[str] = set()
        skills: list[str] = []
        for tag in skill_tags:
            bdi = tag.find("bdi")
            name = _text(bdi) if bdi else _text(tag)
            if name and name != "—" and name not in seen_skills:
                seen_skills.add(name)
                skills.append(name)
        job.skills = skills

        # ── Client profile card ───────────────────────────────────────────────
        client = ClientProfile()

        # Owner name — profile link href contains /users/
        owner_link = soup.find("a", href=re.compile(r"/users/"))
        if owner_link:
            bdi = owner_link.find("bdi")
            client.name = _text(bdi if bdi else owner_link)
            client.profile_url = owner_link.get("href", "")

        # Hiring rate — <label class="label label-rating-excellent">79.17%</label>
        rate_badge = soup.find("label", class_=re.compile(r"label-rating"))
        if rate_badge:
            client.hiring_rate = rate_badge.get_text(strip=True)
            for cls in rate_badge.get("class", []):
                if cls.startswith("label-rating-"):
                    client.hiring_rate_label = cls.replace("label-rating-", "")
                    break

        # Client stats — <td> label span + next sibling <td> for value
        stat_keys = {
            "المشاريع المفتوحة":    "open_projects",
            "مشاريع قيد التنفيذ":  "in_progress_projects",
            "التواصلات الجارية":   "ongoing_communications",
            "تاريخ التسجيل":       "registration_date",
        }
        for td in soup.find_all("td"):
            label_text = td.get_text(strip=True)
            for ar_key, attr in stat_keys.items():
                if ar_key in label_text:
                    next_td = td.find_next_sibling("td")
                    if next_td:
                        setattr(client, attr, next_td.get_text(strip=True))

        job.client = client
        logger.debug(
            f"Parsed sidebar for job {job.id}: "
            f"budget={job.budget!r}, duration={job.duration!r}, "
            f"skills={job.skills}, client={client.name!r}"
        )

    def _parse_questions(self, soup: BeautifulSoup, job: Job) -> None:
        """
        Parse pre-application questions (shown only when logged in).
        Mostaql renders questions in a section titled 'أسئلة قبل التقديم' or similar.
        """
        questions: list[JobQuestion] = []

        # Look for a section/div containing the keyword
        q_section = soup.find(
            string=re.compile(r"أسئلة.*التقديم|أسئلة المشروع|يرجى الإجابة")
        )
        if not q_section:
            return  # No questions for this job

        # Walk up to find the container
        container = q_section.find_parent("div") or q_section.find_parent("section")
        if not container:
            return

        # Each question is typically a <p>, <li>, or labeled block
        for el in container.find_all(["p", "li", "label"]):
            text = el.get_text(strip=True)
            if text and len(text) > 5 and text not in (
                "أسئلة قبل التقديم", "أسئلة المشروع"
            ):
                questions.append(JobQuestion(question=text))

        job.questions = questions
        if questions:
            logger.info(f"Found {len(questions)} pre-application questions for job {job.id}")
