import os
import sys
import argparse
import asyncio
from dotenv import load_dotenv

# Add parent directory to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.ingestion.pipeline import run_ingestion_pipeline
from app.models.schemas import Category

async def main():
    parser = argparse.ArgumentParser(description="LegalX Shorts — Ingestion Pipeline Runner")
    parser.add_argument(
        "--category",
        type=str,
        default="cyber",
        choices=[c.value for c in Category],
        help="Category to ingest (default: cyber)"
    )
    parser.add_argument(
        "--max-docs",
        type=int,
        default=3,
        help="Maximum documents to fetch and process (default: 3)"
    )
    parser.add_argument(
        "--from-date",
        type=str,
        default="1-1-2024",
        help="Start date filter DD-MM-YYYY (default: 1-1-2024 for latest judgments)"
    )
    args = parser.parse_args()

    dev_env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.development")
    load_dotenv(dev_env_path)

    category_enum = Category(args.category)
    print(f"🚀 Starting Ingestion Pipeline for Category: '{category_enum.value}' (Max Docs: {args.max_docs}, From Date: {args.from_date})...\n")

    summary = await run_ingestion_pipeline(
        category=category_enum,
        max_docs_to_fetch=args.max_docs,
        from_date=args.from_date
    )

    print("\n✅ Ingestion Summary:")
    print(f"  • Found count: {summary['found_count']}")
    print(f"  • Docs fetched: {summary['docs_fetched']}")
    print(f"  • Cards staged for review: {summary['cards_staged']}")
    print(f"  • Skipped (already processed): {summary['skipped_duplicate']}")
    print(f"  • Failed: {summary['failed']}")
    print(f"  • Paid IndianKanoon calls: {summary['ikanoon_calls']}")
    print("  • Review them at /preview — nothing is published until approved.")

if __name__ == "__main__":
    asyncio.run(main())
