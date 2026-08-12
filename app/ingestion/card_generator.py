import json
import logging
from typing import Optional
from groq import Groq
from pydantic import ValidationError
from app.config import settings
from app.ingestion.llm_errors import FATAL_LLM_ERRORS, RETRYABLE_LLM_ERRORS
from app.models.schemas import SanitizedContent, CardDraft

logger = logging.getLogger(__name__)

MAX_RETRIES = 3

CARD_GENERATOR_SYSTEM_PROMPT = """Write a high-quality Q&A legal flashcard based ONLY on the provided court judgment sections.

Target audience: General public with no legal background. Plain English, clear, scenario-based. Do not give legal advice or tell the user what to do in their personal life—frame the question and answer around what the court decided in the ruling.

Requirements for JSON response:
1. question: A scenario-based question framed around the court ruling (max 120 characters / ~15-20 words). Example: "Does a company director automatically get cleared in a cheque bounce case just by claiming they weren't involved?"
2. direct_answer: Immediate, direct 1-sentence answer (max 120 characters / ~15-20 words). Example: "No, not automatically. A court cannot guess at the start if the complaint claims they ran daily business."
3. explanation: Plain-language breakdown of the reasoning (~150 words / ~600 characters max so it fits 1 mobile screen without scrolling). End with why this practical legal principle matters.
4. suggested_questions: 2-3 short follow-up questions a curious reader might ask about this ruling (for a future Ask AI feature).

Respond with JSON only matching this schema:
{
  "question": "<scenario-based question>",
  "direct_answer": "<immediate 1-sentence answer>",
  "explanation": "<150 word plain language breakdown>",
  "suggested_questions": ["<question 1>", "<question 2>", "<question 3>"]
}"""

async def generate_card_draft(
    content: SanitizedContent,
    doc_title: str,
    doc_source: str,
    groq_client: Optional[Groq] = None
) -> Optional[CardDraft]:
    """Generate a Q&A flashcard draft using the high-reasoning Groq model (Llama-3.3-70B).
    
    Hardcodes case_reference from API metadata (doc_title and doc_source) to prevent LLM citation hallucinations.
    """
    if not content.sections:
        logger.info("Card generation skipped: no sections present")
        return None

    if groq_client is None:
        groq_client = Groq(api_key=settings.groq_api_key)

    # Build hardcoded, authoritative case reference from API metadata (never let LLM invent it)
    case_ref_parts = [doc_title]
    if doc_source:
        case_ref_parts.append(f"({doc_source})")
    hardcoded_case_reference = " ".join(case_ref_parts)

    # User message: case metadata + structured judgment content
    user_content = f"Case Title: {doc_title}\nCourt: {doc_source}\n\n{content.xml_prompt_block}"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = groq_client.chat.completions.create(
                model=settings.groq_generator_model,
                messages=[
                    # S-3: System role = instructions; user role = untrusted judgment data.
                    # Prevents adversarial document content from overriding card format rules.
                    {"role": "system", "content": CARD_GENERATOR_SYSTEM_PROMPT},
                    {"role": "user", "content": user_content},
                ],
                response_format={"type": "json_object"},
                temperature=0.2
            )
            
            raw_json_str = response.choices[0].message.content or "{}"
            
            # L-2: json imported at module top (not inside loop)
            # L-1: removed dead variable `raw_dict` (was same as raw_json_str, never used)
            data = json.loads(raw_json_str)
            
            # Attach hardcoded case reference
            data["case_reference"] = hardcoded_case_reference
            
            draft = CardDraft.model_validate(data)
            return draft

        except FATAL_LLM_ERRORS as e:
            logger.error("Card Generator aborted — non-retryable Groq error: %s", e)
            return None
        except RETRYABLE_LLM_ERRORS as e:
            logger.warning(
                "Card Generator attempt %d/%d failed (retryable): %s", attempt, MAX_RETRIES, e
            )
        except (ValidationError, ValueError, KeyError, IndexError, TypeError) as e:
            logger.warning(
                "Card Generator attempt %d/%d returned unusable output: %s",
                attempt, MAX_RETRIES, e
            )

    logger.error("Card Generator failed after %d attempts", MAX_RETRIES)
    return None
