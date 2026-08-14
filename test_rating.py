import asyncio
from scraper.mostaql import MostaqlScraper
from scraper.models import Job
from ai.proposal import GeminiProposalGenerator
from config import load_settings

async def main():
    settings = load_settings()
    generator = GeminiProposalGenerator(
        api_keys=[settings.gemini_api_key, settings.gemini_fallback_api_key],
        model_name="gemini-3.5-flash",
        base_proposal_prompt="prompt"
    )
    
    scraper = MostaqlScraper(
        jobs_url=settings.mostaql_jobs_url,
        mostaql_email=settings.mostaql_email,
        mostaql_password=settings.mostaql_password
    )
    await scraper.__aenter__()
    if settings.is_authenticated:
        await scraper.login()
        
    job = Job(id="1268240", url="https://mostaql.com/project/1268240")
    await scraper.fetch_job_details(job)
    await scraper.__aexit__(None, None, None)
    
    prompt = generator._build_prompt(job, is_rating=True)
    
    from google import genai
    from google.genai import types
    client = genai.Client(api_key=settings.gemini_fallback_api_key)
    response = client.models.generate_content(
        model="gemini-3.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.4,
            max_output_tokens=8000,
        ),
    )
    print("FINISH REASON:", response.candidates[0].finish_reason)
    print("TEXT:", response.text)

if __name__ == "__main__":
    asyncio.run(main())
