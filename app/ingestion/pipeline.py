import os
import json
import uuid
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional, Set
from groq import Groq
from app.config import settings, STAGING_DIR as CONFIG_STAGING_DIR
from app.ingestion.indiankanoon_client import BudgetExhaustedError
from app.repository.content_repository import CardSaveError
from app.ingestion.indiankanoon_client import IndianKanoonClient
from app.ingestion.filters import filter_search_results
from app.ingestion.structural_parser import parse_structural_sections
from app.ingestion.sanitizer import sanitize_structured_sections
from app.ingestion.ai_gate import run_ai_gate
from app.ingestion.card_generator import generate_card_draft
from app.models.schemas import Category
from app.repository.content_repository import save_card, card_exists_by_tid

logger = logging.getLogger(__name__)

STAGING_DIR = CONFIG_STAGING_DIR
CANDIDATES_FILE = os.path.join(STAGING_DIR, "candidates.jsonl")
RUN_STATS_FILE = os.path.join(STAGING_DIR, "run_stats.jsonl")

def _calls_made(ik_client) -> Optional[int]:
    """Best-effort paid-call count for run stats; never break a run over it."""
    try:
        return int(ik_client.calls_made)
    except (AttributeError, TypeError, ValueError):
        return None


# Category topic query mapping
CATEGORY_TOPIC_MAP = {
    Category.cyber: "Information Technology Act ORR cyber fraud",
    Category.traffic: "Motor Vehicles Act ORR road accident",
    Category.posco: "Protection of Children from Sexual Offences Act ORR POCSO",
    Category.consumer: "Consumer Protection Act",
    Category.cheque_ni_act: "Negotiable Instruments Act ORR Section 138",
}

def load_seen_tids() -> Set[str]:
    """Load previously processed document TIDs from staging candidate and gate log files to avoid duplicate API calls."""
    seen: Set[str] = set()
    
    # 1. Read from candidates.jsonl
    if os.path.exists(CANDIDATES_FILE):
        try:
            with open(CANDIDATES_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        data = json.loads(line)
                        if "source_tid" in data:
                            seen.add(str(data["source_tid"]))
        except Exception as e:
            logger.warning("Error reading candidates.jsonl for seen TIDs: %s", str(e))
            
    # 2. Read from ai_gate_log.jsonl
    gate_log = os.path.join(STAGING_DIR, "ai_gate_log.jsonl")
    if os.path.exists(gate_log):
        try:
            with open(gate_log, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        data = json.loads(line)
                        if "source_tid" in data:
                            seen.add(str(data["source_tid"]))
        except Exception as e:
            logger.warning("Error reading ai_gate_log.jsonl for seen TIDs: %s", str(e))
            
    return seen

async def run_ingestion_pipeline(
    category: Category = Category.cyber,
    max_docs_to_fetch: int = 5,
    from_date: Optional[str] = "1-1-2024",
    groq_client: Optional[Groq] = None,
    ik_client: Optional[IndianKanoonClient] = None
) -> dict:
    """End-to-end ingestion pipeline runner.
    
    Flow:
    search ➔ filter (dedup) ➔ fetch ➔ parse ➔ sanitize ➔ AI gate ➔ card generator ➔ staging/candidates.jsonl
    """
    os.makedirs(STAGING_DIR, exist_ok=True)

    if ik_client is None:
        ik_client = IndianKanoonClient(
            token=settings.ikanoon_token,
            dev_mode=(settings.environment == "development"),
            max_dev_calls=settings.dev_max_ikanoon_calls,
            max_calls=settings.max_ikanoon_calls,
        )

    if groq_client is None:
        groq_client = Groq(api_key=settings.groq_api_key)

    topic = CATEGORY_TOPIC_MAP.get(category, "Supreme Court of India")
    form_input = ik_client.build_form_input(topic=topic, doctypes="supremecourt", from_date=from_date)

    logger.info("Starting ingestion pipeline for category '%s' with query: '%s'", category.value, form_input)

    # 1. Search IndianKanoon
    raw_results = ik_client.search(form_input, page_num=0)
    found_count = len(raw_results)

    # 2. Deduplicate against previously processed TIDs
    seen_tids = load_seen_tids()
    survivors = filter_search_results(raw_results, seen_tids=seen_tids)

    # Limit to max_docs_to_fetch
    survivors = survivors[:max_docs_to_fetch]
    docs_fetched = 0
    cards_staged = 0
    skipped_duplicate = 0
    failed = 0

    # 3. Process each surviving judgment
    for res in survivors:
        # Authoritative dedup against the real backend. load_seen_tids() only
        # reads local JSONL, which is always empty once Supabase is configured —
        # so without this check every run re-fetched and re-paid for the same
        # documents, then hit the source_tid UNIQUE constraint on insert.
        if card_exists_by_tid(res.tid):
            logger.info("Skipping tid=%s: already processed", res.tid)
            skipped_duplicate += 1
            continue

        # Respectful delay between API calls (non-blocking: time.sleep() would
        # stall the event loop for the whole run).
        await asyncio.sleep(1.0)

        logger.info("Fetching document tid=%s: '%s'", res.tid, res.title)
        try:
            doc = ik_client.fetch_document(res.tid)
        except BudgetExhaustedError:
            logger.warning("IndianKanoon budget exhausted — ending run early")
            break
        except Exception as e:
            logger.error("Failed to fetch tid=%s: %s", res.tid, e)
            failed += 1
            continue
        docs_fetched += 1

        # 4. Parse structural sections
        sections = parse_structural_sections(doc.doc_html)
        if not sections.has_substance():
            logger.info("Skipping tid=%s: thin/procedural order lacking substantive sections", res.tid)
            continue

        # 5. Sanitize sections
        sanitized = sanitize_structured_sections(sections)

        # 6. AI Gate Triage (Llama-3.1-8B)
        # source_tid is passed so the gate log records which document was
        # rejected, which is what keeps it out of the next run's fetch list.
        verdict = await run_ai_gate(sanitized, groq_client=groq_client, source_tid=res.tid)
        if not verdict or not verdict.card_worthy:
            logger.info("AI Gate rejected tid=%s: %s", res.tid, verdict.reasoning if verdict else "No verdict")
            continue

        if verdict.suggested_category != category.value:
            logger.info(
                "AI Gate category mismatch for tid=%s: expected '%s', got '%s' (reasoning: %s)",
                res.tid, category.value, verdict.suggested_category, verdict.reasoning
            )
            continue

        # 7. Card Generation (Llama-3.3-70B Q&A format)
        draft = await generate_card_draft(
            content=sanitized,
            doc_title=doc.title,
            doc_source=doc.docsource,
            groq_client=groq_client
        )
        if not draft:
            logger.warning("Card generator failed to produce draft for tid=%s", res.tid)
            continue

        # 8. Save candidate via repository layer (Supabase, or JSONL locally)
        now_iso = datetime.now(timezone.utc).isoformat()
        card_id = str(uuid.uuid4())

        candidate_record = {
            "id": card_id,
            "content_type": "judgment_summary",
            "category": category.value,
            "title": draft.question,
            "question": draft.question,
            "direct_answer": draft.direct_answer,
            "explanation": draft.explanation,
            "card_text": f"Q: {draft.question}\n\nA: {draft.direct_answer}\n\n{draft.explanation}",
            "case_reference": draft.case_reference,
            "suggested_questions": draft.suggested_questions,
            "source_url": f"https://indiankanoon.org/doc/{res.tid}/",
            "source_tid": res.tid,
            "content_hash": sanitized.content_hash,
            # Judgment cards previously omitted this field entirely, so nothing
            # marked them as awaiting review and the JSONL feed served them
            # straight to users. Cards are staged, never auto-published.
            "is_published": False,
            # Set by the reviewer at approval time, not at creation time.
            "published_at": None,
            "created_at": now_iso
        }

        try:
            save_card(candidate_record)
        except CardSaveError as e:
            logger.error("Could not stage card for tid=%s: %s", res.tid, e)
            failed += 1
            continue

        cards_staged += 1
        logger.info("Successfully staged Q&A flashcard for tid=%s (id=%s)", res.tid, card_id)

    # 9. Write run stats to staging/run_stats.jsonl
    summary = {
        "status": "success",
        "category": category.value,
        "found_count": found_count,
        "docs_fetched": docs_fetched,
        # Cards are STAGED for human review, never published by the pipeline.
        # The old key name ("cards_published") described something the pipeline
        # must not do.
        "cards_staged": cards_staged,
        "skipped_duplicate": skipped_duplicate,
        "failed": failed,
        "ikanoon_calls": _calls_made(ik_client),
    }

    run_stats = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "query_used": form_input,
        **summary,
    }

    try:
        os.makedirs(os.path.dirname(RUN_STATS_FILE) or ".", exist_ok=True)
        with open(RUN_STATS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(run_stats) + "\n")
    except OSError as e:
        logger.warning("Could not write run stats: %s", e)

    logger.info("Pipeline completed: %s", summary)
    return summary
