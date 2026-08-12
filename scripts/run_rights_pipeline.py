import os
import sys
from pathlib import Path

# Add root directory to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import argparse
import asyncio
import logging
from app.models.schemas import Category
from app.ingestion.rights_pipeline import run_rights_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("run_rights_pipeline")

async def main():
    parser = argparse.ArgumentParser(description="Run LegalX Shorts Statutory Rights Pipeline")
    parser.add_argument(
        "--act",
        type=str,
        default="Protection of Children from Sexual Offences Act",
        help="Name of the Bare Act (default: POCSO Act)"
    )
    parser.add_argument(
        "--category",
        type=str,
        default="posco",
        choices=[c.value for c in Category],
        help="Category enum string (default: posco)"
    )
    parser.add_argument(
        "--max-sections",
        type=int,
        default=3,
        help="Max act sections to fetch and process (default: 3)"
    )

    args = parser.parse_args()

    selected_category = Category(args.category)

    print(f"\n🚀 Starting Statutory Rights Pipeline for Act: '{args.act}' (Category: '{selected_category.value}', Max Sections: {args.max_sections})...\n")

    summary = await run_rights_pipeline(
        act_name=args.act,
        category=selected_category,
        max_sections=args.max_sections
    )

    print(f"\n✅ Rights Ingestion Summary:")
    print(f"  • Act Name: {summary['act_name']}")
    print(f"  • Category: {summary['category']}")
    print(f"  • Found count: {summary['found_count']}")
    print(f"  • Sections fetched: {summary['sections_fetched']}")
    print(f"  • Cards staged (is_published=False): {summary['cards_staged']}\n")

if __name__ == "__main__":
    asyncio.run(main())
