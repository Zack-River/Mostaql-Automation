import asyncio
import os
from dotenv import load_dotenv

from scraper.mostaql import MostaqlScraper
from ai.proposal import GeminiProposalGenerator
from config import load_settings

async def main():
    print("Loading settings...")
    settings = load_settings()
    
    print("Initializing scraper...")
    scraper = MostaqlScraper(
        jobs_url=settings.mostaql_jobs_url,
        mostaql_email=settings.mostaql_email,
        mostaql_password=settings.mostaql_password
    )
    
    await scraper.__aenter__()
    if settings.is_authenticated:
        await scraper.login()

    print("Fetching jobs...")
    jobs = await scraper.fetch_jobs_list()
    if not jobs:
        print("No jobs found.")
        await scraper.__aexit__(None, None, None)
        return
        
    latest_job = jobs[0]
    print(f"Latest Job Title: {latest_job.title}")
    print(f"Latest Job URL: {latest_job.url}")
    
    print("\nFetching full job details...")
    # Get full details (description, questions)
    await scraper.fetch_job_details(latest_job)
    await scraper.__aexit__(None, None, None)
    
    print("\nInitializing AI generator...")
    generator = GeminiProposalGenerator(
        api_keys=[settings.gemini_api_key, settings.gemini_fallback_api_key],
        model_name=settings.gemini_model,
        base_proposal_prompt=settings.base_proposal_prompt
    )
    
    print("\nGenerating proposal...")
    proposal = await generator.generate(latest_job)
    
    print("\n" + "="*50)
    print("✨ GENERATED PROPOSAL ✨")
    print("="*50)
    print(proposal)
    print("="*50)

if __name__ == "__main__":
    asyncio.run(main())
