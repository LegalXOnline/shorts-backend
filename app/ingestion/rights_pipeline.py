import os
import json
import uuid
import asyncio
import logging
import hashlib
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from typing import Optional
from groq import Groq

from app.config import settings, STAGING_DIR as CONFIG_STAGING_DIR
from app.ingestion.indiankanoon_client import IndianKanoonClient, BudgetExhaustedError
from app.ingestion.rights_summarizer import summarize_rights_section
from app.repository.content_repository import save_card, card_exists_by_tid, CardSaveError
from app.models.schemas import Category, ContentType

logger = logging.getLogger(__name__)

STAGING_DIR = CONFIG_STAGING_DIR
RUN_STATS_FILE = os.path.join(STAGING_DIR, "run_stats.jsonl")

def _calls_made(ik_client) -> Optional[int]:
    """Best-effort paid-call count for run stats; never break a run over it."""
    try:
        return int(ik_client.calls_made)
    except (AttributeError, TypeError, ValueError):
        return None



def master_tid_for(tid: str) -> str:
    """Stable dedup key for a consolidated multi-section 'master' Act card.

    The single-card path saved this prefixed key but checked dedup against the
    bare tid, so the key written never matched the key queried and every run
    re-fetched the same sections.
    """
    return f"act_{tid}"

def extract_section_text(doc_html: str) -> str:
    """Extract clean section text from IndianKanoon statutory HTML."""
    if not doc_html:
        return ""
    soup = BeautifulSoup(doc_html, "html.parser")
    
    # Remove navigation, headers, and footer tags
    for tag in soup.find_all(["script", "style", "a", "form"]):
        tag.decompose()
        
    text = soup.get_text(separator="\n", strip=True)
    return text

async def run_rights_pipeline(
    act_name: str = "Protection of Children from Sexual Offences Act",
    category: Category = Category.posco,
    max_sections: int = 3,
    single_card: bool = True,
    groq_client: Optional[Groq] = None,
    ik_client: Optional[IndianKanoonClient] = None
) -> dict:
    """End-to-end statutory rights & laws ingestion pipeline runner.

    Flow:
    search IndianKanoon (doctypes: acts) ➔ dedup ➔ fetch section HTML ➔ parse ➔ summarize ➔ save_card (content_type=rights_explainer, is_published=false)
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

    form_input = f"{act_name} doctypes: acts"
    logger.info("Starting Rights Pipeline for Act: '%s' (Category: '%s', Single Card: %s)", act_name, category.value, single_card)

    # 1. Search IndianKanoon for statutory Act sections
    raw_results = ik_client.search(form_input, page_num=0)
    found_count = len(raw_results)

    # Filter to statutory section sources
    act_results = []
    for r in raw_results:
        docsource_lower = (r.docsource or "").lower()
        title_lower = (r.title or "").lower()
        if "section" in docsource_lower or "union of india" in docsource_lower or "section" in title_lower:
            act_results.append(r)

    if not act_results:
        act_results = raw_results

    sections_fetched = 0
    cards_staged = 0

    if single_card:
        # Consolidate top sections into 1 single master card for the Act
        combined_texts = []
        source_tids = []
        for res in act_results[:max_sections]:
            # Check BOTH keys: the bare tid (section-by-section mode) and the
            # act_ prefixed master key this branch actually writes.
            if card_exists_by_tid(res.tid) or card_exists_by_tid(master_tid_for(res.tid)):
                logger.info("Skipping section tid=%s: already processed", res.tid)
                continue
            await asyncio.sleep(0.5)
            try:
                doc = ik_client.fetch_document(res.tid)
            except BudgetExhaustedError:
                logger.warning("IndianKanoon budget exhausted — ending run early")
                break
            except Exception as e:
                logger.error("Failed to fetch section tid=%s: %s", res.tid, e)
                continue
            clean_text = extract_section_text(doc.doc_html)
            if len(clean_text.strip()) > 30:
                combined_texts.append(f"{res.title}:\n{clean_text}")
                source_tids.append(res.tid)
                sections_fetched += 1

        if combined_texts:
            full_act_text = "\n\n".join(combined_texts)
            draft = await summarize_rights_section(
                section_title=f"{act_name} Overview",
                section_text=full_act_text,
                act_title=act_name,
                category=category,
                groq_client=groq_client
            )

            if draft:
                now_iso = datetime.now(timezone.utc).isoformat()
                card_id = str(uuid.uuid4())
                master_tid = master_tid_for(source_tids[0]) if source_tids else f"act_{uuid.uuid4()}"
                content_hash = hashlib.sha256(full_act_text.encode("utf-8")).hexdigest()

                card_record = {
                    "id": card_id,
                    "content_type": ContentType.rights_explainer.value,
                    "category": category.value,
                    "title": draft.title,
                    "question": draft.title,
                    "direct_answer": draft.summary,
                    "explanation": draft.summary,
                    "card_text": draft.summary,
                    # Use the model's extracted citation, same as the
                    # section-by-section branch. The year was hardcoded to
                    # 2012 (correct only for POCSO), which produced factually
                    # wrong citations for every other Act.
                    "case_reference": draft.statute_reference or act_name,
                    "suggested_questions": [],
                    "source_url": f"https://indiankanoon.org/doc/{source_tids[0]}/" if source_tids else None,
                    "source_tid": master_tid,
                    "content_hash": content_hash,
                    "is_published": False,  # Staged for Human Review!
                    "published_at": None,   # Set by the reviewer on approval
                    "created_at": now_iso
                }

                try:
                    save_card(card_record)
                    cards_staged += 1
                    logger.info("Successfully staged single Master Rights card for Act '%s' (id=%s)", act_name, card_id)
                except CardSaveError as e:
                    logger.error("Could not stage master rights card: %s", e)

    else:
        # Process section-by-section
        for res in act_results[:max_sections]:
            if card_exists_by_tid(res.tid):
                logger.info("Skipping section tid=%s: already processed", res.tid)
                continue
            await asyncio.sleep(1.0)
            try:
                doc = ik_client.fetch_document(res.tid)
            except BudgetExhaustedError:
                logger.warning("IndianKanoon budget exhausted — ending run early")
                break
            except Exception as e:
                logger.error("Failed to fetch section tid=%s: %s", res.tid, e)
                continue
            sections_fetched += 1
            clean_text = extract_section_text(doc.doc_html)
            if len(clean_text.strip()) < 30:
                continue

            draft = await summarize_rights_section(
                section_title=res.title,
                section_text=clean_text,
                act_title=act_name,
                category=category,
                groq_client=groq_client
            )
            if not draft:
                continue

            now_iso = datetime.now(timezone.utc).isoformat()
            card_id = str(uuid.uuid4())
            content_hash = hashlib.sha256(clean_text.encode("utf-8")).hexdigest()

            card_record = {
                "id": card_id,
                "content_type": ContentType.rights_explainer.value,
                "category": category.value,
                "title": draft.title,
                "question": draft.title,
                "direct_answer": draft.summary,
                "explanation": draft.summary,
                "card_text": draft.summary,
                "case_reference": draft.statute_reference,
                "suggested_questions": [],
                "source_url": f"https://indiankanoon.org/doc/{res.tid}/",
                "source_tid": res.tid,
                "content_hash": content_hash,
                "is_published": False,
                "published_at": None,   # Set by the reviewer on approval
                "created_at": now_iso
            }

            try:
                save_card(card_record)
                cards_staged += 1
            except CardSaveError as e:
                logger.error("Could not stage rights card for tid=%s: %s", res.tid, e)

    summary = {
        "status": "success",
        "act_name": act_name,
        "category": category.value,
        "found_count": found_count,
        "sections_fetched": sections_fetched,
        "cards_staged": cards_staged,
        "ikanoon_calls": _calls_made(ik_client),
    }

    try:
        os.makedirs(os.path.dirname(RUN_STATS_FILE) or ".", exist_ok=True)
        with open(RUN_STATS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "pipeline": "rights",
                **summary,
            }) + "\n")
    except OSError as e:
        logger.warning("Could not write run stats: %s", e)

    logger.info("Rights Pipeline completed: %s", summary)
    return summary
