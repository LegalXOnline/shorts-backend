import logging
from datetime import datetime, timezone
from typing import Optional
from groq import Groq
from pydantic import ValidationError
from app.config import settings
from app.ingestion.llm_errors import FATAL_LLM_ERRORS, RETRYABLE_LLM_ERRORS
from app.models.schemas import SanitizedContent, GateVerdict
from app.repository import content_repository as repo

logger = logging.getLogger(__name__)

MAX_RETRIES = 3

GATE_SYSTEM_PROMPT = """You are reviewing Indian court judgments to decide if they are suitable for a general-audience legal awareness flashcard feed.

A judgment qualifies if:
1. It is a final, substantive decision (not a procedural/interim order or simple adjournment notice).
2. It has a clear legal holding or decision.
3. A non-lawyer would find it understandable, relevant, and educational.

Map the judgment to the single best category based on the primary governing Act/subject in the text:
- cyber: Information Technology Act (IT Act), cybercrime, digital evidence, data privacy
- traffic: Motor Vehicles Act, road accidents, drunk driving, traffic offenses
- posco: Protection of Children from Sexual Offences Act (POCSO)
- consumer: Consumer Protection Act, deficiency of service, consumer rights
- cheque_ni_act: Negotiable Instruments Act (Section 138 cheque bounce)
- other: Any other legal Act/topic (e.g. Legal Metrology Act, general IPC crimes, bail procedure, land acquisition, tax)

Respond with JSON only:
{
  "card_worthy": true | false,
  "reasoning": "<one sentence explanation>",
  "is_final_judgment": true | false,
  "suggested_category": "cyber" | "traffic" | "posco" | "consumer" | "cheque_ni_act" | "other"
}"""

async def run_ai_gate(
    content: SanitizedContent,
    groq_client: Optional[Groq] = None,
    source_tid: Optional[str] = None,
) -> Optional[GateVerdict]:
    """Run the AI Gate on sanitized judgment sections using the fast Groq model.

    Retries on malformed model output. Non-retryable API errors (bad key,
    quota) fail immediately instead of burning three attempts. Every decision
    is logged with its source_tid so rejected documents are not re-fetched.
    """
    if not content.sections:
        logger.info("AI Gate skipped: no sections present")
        return None

    if groq_client is None:
        groq_client = Groq(api_key=settings.groq_api_key)

    verdict: Optional[GateVerdict] = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = groq_client.chat.completions.create(
                model=settings.groq_gate_model,
                messages=[
                    # S-3: Instructions in system role; untrusted judgment content in user role.
                    # This creates architectural separation so document text cannot override gate rules.
                    {"role": "system", "content": GATE_SYSTEM_PROMPT},
                    {"role": "user", "content": content.xml_prompt_block},
                ],
                response_format={"type": "json_object"},
                temperature=0.1
            )

            raw_json = response.choices[0].message.content or "{}"
            verdict = GateVerdict.model_validate_json(raw_json)
            break  # Successfully parsed
        except FATAL_LLM_ERRORS as e:
            # Auth/permission/quota problems will not resolve on retry, and the
            # old `except (ValidationError, Exception)` made them look like
            # malformed JSON in the logs.
            logger.error("AI Gate aborted — non-retryable Groq error: %s", e)
            return None
        except RETRYABLE_LLM_ERRORS as e:
            logger.warning(
                "AI Gate attempt %d/%d failed (retryable): %s", attempt, MAX_RETRIES, e
            )
        except (ValidationError, ValueError, KeyError, IndexError, TypeError) as e:
            logger.warning(
                "AI Gate attempt %d/%d returned unusable output: %s", attempt, MAX_RETRIES, e
            )

    if verdict is None:
        logger.error("AI Gate failed after %d attempts", MAX_RETRIES)
        return None

    # Log verdict to JSONL file for audit trail
    _log_ai_gate_decision(content.content_hash, verdict, source_tid=source_tid)

    return verdict

def _log_ai_gate_decision(
    content_hash: str,
    verdict: GateVerdict,
    source_tid: Optional[str] = None,
) -> None:
    """Log AI gate decision to staging/ai_gate_log.jsonl for reviewer auditing.

    source_tid is essential, not decorative: load_seen_tids() and
    card_exists_by_tid() both look it up in this file to avoid re-fetching
    documents the gate already rejected. It was previously omitted, so every
    rejected judgment was re-fetched and re-billed on every single run.
    """
    log_entry = {
        "content_hash": content_hash,
        "source_tid": str(source_tid) if source_tid is not None else None,
        "card_worthy": verdict.card_worthy,
        "reasoning": verdict.reasoning,
        "is_final_judgment": verdict.is_final_judgment,
        "suggested_category": verdict.suggested_category,
        "logged_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        # Resolved at call time, not import time: binding the path as a
        # module-level constant here meant it could not be redirected (tests
        # writing into the real staging directory), and it would ignore any
        # later reconfiguration of the staging root.
        repo._append_jsonl(repo.AI_GATE_LOG_FILE, log_entry)
    except OSError as e:
        logger.error("Failed to write to ai_gate_log.jsonl: %s", str(e))

