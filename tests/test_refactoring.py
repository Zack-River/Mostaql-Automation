import os
import sys
import asyncio
import unittest
import warnings

# Ensure bot modules can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from scraper.models import Job
from ai.proposal import GeminiProposalGenerator

# We need to load dotenv to get GEMINI_API_KEY if testing locally
from dotenv import load_dotenv
load_dotenv()

class TestRefactoredAI(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        # Ignore resource warnings from httpx/asyncio
        warnings.simplefilter("ignore", ResourceWarning)
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise unittest.SkipTest("GEMINI_API_KEY not found in environment")
        
        from ai.prompts import MOSTAQL_SYSTEM_PROMPT
        cls.ai = GeminiProposalGenerator([api_key], "gemini-3.5-flash", MOSTAQL_SYSTEM_PROMPT)

    async def run_scenario(self, title, description, budget, duration, questions=None, user_notes=""):
        from scraper.models import ClientProfile
        job = Job(
            id="123",
            url="http://example.com",
            title=title,
            description_snippet=description,
            full_description=description,
            budget=budget,
            duration=duration,
            client=ClientProfile(name="Test Client"),
            time_posted="2 ساعات",
            proposals_count="10",
        )
        
        # 1. Rate Job
        rating = await self.ai.rate_job(job)
        
        # 2. Suggest Apply Params
        params = await self.ai.suggest_apply_params(job, form_questions=questions or [])
        
        # 3. Generate Proposal
        proposal = await self.ai.generate(job, user_notes=user_notes)
        
        return rating, params, proposal

    async def test_10_real_estate_edge_case(self):
        print("\n--- Test 10: Real Estate Edge Case (Negative Test) ---")
        rating, params, proposal = await self.run_scenario(
            title="تطبيق لبيع وايجار عقارات وسيارات",
            description="مطلوب تطبيق لبيع وايجار عقارات وسيارات",
            budget="$1000.00 - $2500.00",
            duration="7 أيام"
        )
        
        self.assertIsNotNone(rating)
        self.assertIsNotNone(proposal)
        self.assertIsNotNone(params)

        print(f"Rating:\n{rating}\n")
        print(f"Proposal:\n{proposal}\n")
        print(f"Params:\n{params}\n")

        # Semantic Checks:
        # 1. Proposal must NOT assert complex systems as facts without conditional language
        factual_forbidden = [r"(سيحدث|يجب أن يحتوي|نحتاج إلى) لوحة تحكم",
                             r"سنبني.*API",
                             r"سنقوم بعمل.*بوابة دفع",
                             r"خرائط Google",
                             r"Double Booking"]
        for regex in factual_forbidden:
            self.assertNotRegex(proposal, regex, f"Unsupported certainty detected: {regex}")

        # 2. Should use conditional language if it mentions inferred requirements
        if "حجز" in proposal or "إيجار" in proposal:
            self.assertRegex(proposal, r'(إذا|لو|قد|في حال)', "Inferences about booking must use conditional language.")

        # 3. Should ask exactly one smart question or zero
        question_count = proposal.count('؟')
        self.assertTrue(question_count <= 1, "Must ask at most one smart question.")

    async def test_01_simple_clear_project(self):
        print("\n--- Test 1: Simple Clear Project ---")
        rating, params, proposal = await self.run_scenario(
            title="تعديل صفحة هبوط HTML CSS",
            description="مطلوب تعديل ألوان صفحة هبوط HTML CSS موجودة لتتناسب مع الهوية الجديدة. العمل لا يتطلب إضافة أي صفحات جديدة.",
            budget="$25.00 - $50.00",
            duration="يوم واحد"
        )
        
        self.assertIsNotNone(rating)
        self.assertIsNotNone(proposal)
        
        print(f"Rating:\n{rating}\n")
        print(f"Proposal:\n{proposal}\n")
        
        # Should be short and no fake questions
        self.assertNotRegex(proposal, r'\?|؟', "Simple clear project should not have fake questions.")

    async def test_06_qa_cross_platform(self):
        print("\n--- Test 6: QA Cross-Platform (BeePlayer) ---")
        rating, params, proposal = await self.run_scenario(
            title="مختبر برمجيات لتطبيق فيديو",
            description="نبحث عن مهندس جودة لاختبار تطبيق بث محتوى مرئي على الموبايل والشاشات الذكية.",
            budget="$250.00 - $500.00",
            duration="10 أيام"
        )
        # Should use the specific capability from the profile but NOT mention BeePlayer
        self.assertNotIn("BeePlayer", proposal, "Should NOT cite previous project names.")
        self.assertRegex(proposal, r'(تلفاز|شاشات|منصات)', "Should mention relevant cross-platform TV/Mobile testing capability.")

    async def test_07_nextjs_supabase(self):
        print("\n--- Test 7: Next.js Supabase Timed Exams ---")
        rating, params, proposal = await self.run_scenario(
            title="بناء نظام اختبارات",
            description="نحتاج بناء نظام اختبارات اونلاين للطلاب بوقت محدد ومربوط بـ Supabase.",
            budget="$500.00 - $1000.00",
            duration="20 أيام"
        )
        # Should use specific Next.js/Supabase exam capability but NOT mention the old project name
        self.assertNotIn("ShopStream", proposal, "Should NOT cite previous project names.")
        self.assertRegex(proposal, r'(server-side|validation|تصحيح)', "Should use specific Next.js/Supabase exam capability.")

    async def test_08_forced_apply(self):
        print("\n--- Test 8: Forced Apply ---")
        rating, params, proposal = await self.run_scenario(
            title="تطبيق لبيع وايجار عقارات وسيارات",
            description="تطبيق لبيع وايجار عقارات وسيارات",
            budget="$25.00 - $50.00",
            duration="يوم واحد",
            user_notes="Forced Apply"
        )
        
        # Even with Forced Apply, the rating evaluator should recognize this is impossible
        self.assertRegex(rating, r'تجاهل|Skip|فرصة ضعيفة', "Rating should remain accurate and not be artificially boosted.")
        # But params should still be generated practically
        self.assertTrue(int(params.get('price', 0)) > 0, "Should generate practical price despite bad rating.")

if __name__ == '__main__':
    unittest.main()
