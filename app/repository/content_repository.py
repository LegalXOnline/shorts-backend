import os
import re
import json
import base64
import logging
import tempfile
from typing import Optional, List, Tuple
from datetime import datetime
from contextlib import contextmanager

from app.config import STAGING_DIR as CONFIG_STAGING_DIR
from app.lib.supabase_client import (
    get_supabase_client,
    get_supabase_read_client,
    is_supabase_configured,
)
from app.models.schemas import Category, ContentType

logger = logging.getLogger(__name__)

# Absolute paths (see config.STAGING_DIR) so behaviour does not depend on CWD.
STAGING_DIR = CONFIG_STAGING_DIR
CANDIDATES_FILE = os.path.join(STAGING_DIR, "candidates.jsonl")
AI_GATE_LOG_FILE = os.path.join(STAGING_DIR, "ai_gate_log.jsonl")
# Dead-letter file for records the database rejected. Never read by the feed.
FAILED_SAVES_FILE = os.path.join(STAGING_DIR, "failed_saves.jsonl")

TABLE = "shorts_cards"


class CardSaveError(Exception):
    """Raised when a card could not be persisted to the configured backend."""


# ── 0. JSONL helpers ─────────────────────────────────────────────────────────

@contextmanager
def _locked(path: str, mode: str):
    """Open a staging file holding an exclusive advisory lock for the whole body.

    The previous implementation released the lock before the buffered write was
    flushed, and the preview endpoints did not lock at all — so a concurrent
    pipeline append could be lost during a read-modify-write cycle.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    f = open(path, mode, encoding="utf-8")
    locked = False
    try:
        try:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            locked = True
        except (ImportError, OSError):
            pass
        yield f
        f.flush()
        os.fsync(f.fileno())
    finally:
        if locked:
            try:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
        f.close()


def _append_jsonl(path: str, record: dict) -> None:
    with _locked(path, "a") as f:
        f.write(json.dumps(record) + "\n")


def read_jsonl(path: str) -> List[dict]:
    """Read a JSONL staging file, skipping (and reporting) corrupt lines."""
    records: List[dict] = []
    if not os.path.exists(path):
        return records

    skipped = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    skipped += 1
                    logger.warning("Skipping malformed JSON at %s:%d", path, line_no)
    except OSError as e:
        logger.error("Error reading %s: %s", path, e)
        return records

    if skipped:
        logger.warning("Skipped %d malformed line(s) in %s", skipped, path)
    return records


def rewrite_jsonl(path: str, records: List[dict]) -> None:
    """Atomically replace a JSONL file.

    Writes to a temp file in the same directory then os.replace()s it, so a
    crash mid-write can never truncate the staging file.
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    dir_name = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, prefix=".tmp-", suffix=".jsonl")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


# ── 1. Cursor encoding ───────────────────────────────────────────────────────
# The feed is ordered by (published_at DESC, id DESC), so a cursor must carry
# both parts to be a stable keyset. An id alone cannot resume the scan.

def encode_cursor(published_at: str, card_id: str) -> str:
    raw = f"{published_at}|{card_id}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


# Card ids are UUIDs in Postgres but may be arbitrary slugs in local JSONL, so
# validate against a charset that is safe to interpolate rather than requiring a
# strict UUID — otherwise a slug-id deployment silently loses pagination and
# clients loop on page 1 forever.
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def decode_cursor(cursor: str) -> Optional[Tuple[str, str]]:
    """Decode and validate an opaque cursor. Returns None if malformed.

    Both components are strictly validated because they are interpolated into a
    PostgREST filter expression.
    """
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(cursor + padding).decode("utf-8")
        published_at, card_id = raw.split("|", 1)
    except (ValueError, TypeError, UnicodeDecodeError):
        logger.info("Rejected malformed feed cursor")
        return None

    if not _SAFE_ID.match(card_id):
        logger.info("Rejected feed cursor with unsafe id component")
        return None

    try:
        datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        logger.info("Rejected feed cursor with invalid timestamp")
        return None

    return published_at, card_id


# ── 2. Save Card ─────────────────────────────────────────────────────────────

def save_card(card_data: dict) -> str:
    """Persist a card to Supabase, or to JSONL staging when Supabase is unset.

    A Supabase failure is NOT silently downgraded to a JSONL write any more.
    That old fallback meant a transient DB error (most often the source_tid
    UNIQUE violation) pushed an unreviewed card into the file the public feed
    reads. Failures now go to a dead-letter file and raise.
    """
    card_id = card_data.get("id", "")

    if is_supabase_configured():
        client = get_supabase_client()
        if client is None:
            raise CardSaveError("Supabase is configured but the client failed to initialize")
        try:
            client.table(TABLE).insert(_to_db_record(card_data)).execute()
            logger.info("Saved card %s to Supabase '%s'", card_id, TABLE)
            return card_id
        except Exception as e:
            message = str(e)
            if _is_duplicate_error(message):
                logger.info(
                    "Card %s already exists (source_tid=%s) — skipping",
                    card_id, card_data.get("source_tid")
                )
                return card_id
            logger.error("Failed to insert card %s into Supabase: %s", card_id, message)
            _record_failed_save(card_data, message)
            raise CardSaveError(f"Supabase insert failed for card {card_id}: {message}") from e

    # Supabase not configured: local staging is the intended backend.
    _append_jsonl(CANDIDATES_FILE, card_data)
    logger.info("Saved card %s to JSONL staging file '%s'", card_id, CANDIDATES_FILE)
    return card_id


def _to_db_record(card_data: dict) -> dict:
    """Map a candidate record onto shorts_cards columns."""
    is_published = bool(card_data.get("is_published", False))
    # published_at is meaningless while a card is unreviewed, and the feed
    # orders by it, so send NULL rather than a misleading creation timestamp.
    published_at = card_data.get("published_at") if is_published else None

    return {
        "id": card_data.get("id", ""),
        "content_type": card_data.get("content_type", ContentType.judgment_summary.value),
        "category": card_data.get("category"),
        "title": card_data.get("title", ""),
        "question": card_data.get("question", ""),
        "direct_answer": card_data.get("direct_answer", ""),
        "explanation": card_data.get("explanation", ""),
        "card_text": card_data.get("card_text", ""),
        "case_reference": card_data.get("case_reference", ""),
        "suggested_questions": card_data.get("suggested_questions", []),
        "source_url": card_data.get("source_url"),
        "source_tid": card_data.get("source_tid"),
        "content_hash": card_data.get("content_hash", ""),
        "is_published": is_published,
        "published_at": published_at,
        "created_at": card_data.get("created_at"),
    }


def _is_duplicate_error(message: str) -> bool:
    lowered = message.lower()
    return "23505" in lowered or "duplicate key" in lowered or "already exists" in lowered


def _record_failed_save(card_data: dict, error: str) -> None:
    try:
        _append_jsonl(FAILED_SAVES_FILE, {"error": error, "card": card_data})
    except OSError as e:
        logger.error("Could not write dead-letter record: %s", e)


# ── 3. Deduplication Check ────────────────────────────────────────────────────

def card_exists_by_tid(source_tid: str) -> bool:
    """Check if a document TID has already been processed and saved."""
    if not source_tid:
        return False

    tid = str(source_tid)

    if is_supabase_configured():
        client = get_supabase_client()
        if client is not None:
            try:
                res = client.table(TABLE).select("id").eq("source_tid", tid).limit(1).execute()
                if res.data:
                    return True
            except Exception as e:
                logger.error("Supabase deduplication check error: %s — checking JSONL", e)

    # JSONL check across candidates.jsonl & ai_gate_log.jsonl. The gate log
    # covers documents that were fetched and rejected, so a rejected judgment
    # is not re-fetched (and re-billed) on every subsequent run.
    for path in (CANDIDATES_FILE, AI_GATE_LOG_FILE):
        for data in read_jsonl(path):
            if str(data.get("source_tid")) == tid:
                return True

    return False


# ── 4. Feed Retrieval ─────────────────────────────────────────────────────────

def get_feed(
    category: Optional[Category] = None,
    content_type: ContentType = ContentType.judgment_summary,
    cursor: Optional[str] = None,
    limit: int = 20
) -> Tuple[List[dict], Optional[str]]:
    """Retrieve published feed cards. Uses Supabase if configured, else JSONL.

    Only cards with is_published = true are ever returned, on every backend.
    """
    if is_supabase_configured():
        try:
            return _get_feed_supabase(category, content_type, cursor, limit)
        except Exception as e:
            # Do not fall through to JSONL here: the local file is stale
            # relative to the database, and serving it would silently mix two
            # sources of truth. Surface the failure instead.
            logger.error("Supabase get_feed error: %s", e)
            raise

    return _get_feed_jsonl(category, content_type, cursor, limit)


def _get_feed_supabase(
    category: Optional[Category],
    content_type: ContentType,
    cursor: Optional[str],
    limit: int,
) -> Tuple[List[dict], Optional[str]]:
    client = get_supabase_read_client()
    if client is None:
        # No anon key configured. Fall back to the service client but make the
        # loss of RLS protection explicit rather than invisible.
        logger.warning(
            "SUPABASE_ANON_KEY is not set — serving the public feed with the "
            "service-role key, which bypasses row level security. Set an anon "
            "key so the is_published RLS policy applies as defence in depth."
        )
        client = get_supabase_client()
    if client is None:
        raise CardSaveError("No Supabase client available for reads")

    query = (
        client.table(TABLE)
        .select("*")
        .eq("is_published", True)
        .eq("content_type", content_type.value)
    )

    if category is not None:
        query = query.eq("category", category.value)

    # Keyset pagination. The previous implementation accepted a cursor,
    # returned a next_cursor, and applied neither — so every request returned
    # page 1 and clients paginated forever over the same rows.
    if cursor:
        decoded = decode_cursor(cursor)
        if decoded is not None:
            published_at, card_id = decoded
            query = query.or_(
                f'published_at.lt."{published_at}",'
                f'and(published_at.eq."{published_at}",id.lt."{card_id}")'
            )

    query = query.order("published_at", desc=True).order("id", desc=True).limit(limit + 1)

    rows = query.execute().data or []
    return _paginate(rows, limit)


def _get_feed_jsonl(
    category: Optional[Category],
    content_type: ContentType,
    cursor: Optional[str],
    limit: int,
) -> Tuple[List[dict], Optional[str]]:
    cards = read_jsonl(CANDIDATES_FILE)

    # CRITICAL: the is_published filter. Without it this path served
    # unreviewed, unvetted AI output — including cards explicitly staged for
    # human review — to the public feed.
    filtered = [
        c for c in cards
        if c.get("is_published") is True
        and c.get("content_type") == content_type.value
    ]
    if category is not None:
        filtered = [c for c in filtered if c.get("category") == category.value]

    # Match the database ordering so cursors behave identically on both paths.
    filtered.sort(key=_sort_key, reverse=True)

    if cursor:
        decoded = decode_cursor(cursor)
        if decoded is not None:
            filtered = [c for c in filtered if _sort_key(c) < decoded]

    return _paginate(filtered, limit)


def _sort_key(card: dict) -> Tuple[str, str]:
    return (str(card.get("published_at") or ""), str(card.get("id") or ""))


def _paginate(rows: List[dict], limit: int) -> Tuple[List[dict], Optional[str]]:
    """Slice one over-fetched page and derive the next cursor from its last row."""
    has_more = len(rows) > limit
    page = rows[:limit]

    next_cursor = None
    if has_more and page:
        last = page[-1]
        if last.get("published_at") and last.get("id"):
            next_cursor = encode_cursor(str(last["published_at"]), str(last["id"]))

    return page, next_cursor


# ── 5. Review actions (used by the preview tool) ──────────────────────────────

def set_published(card_id: str, published: bool, published_at: Optional[str]) -> bool:
    """Publish or unpublish a single card. Returns False if it was not found."""
    if is_supabase_configured():
        client = get_supabase_client()
        if client is None:
            raise CardSaveError("Supabase configured but client unavailable")
        payload = {"is_published": published, "published_at": published_at if published else None}
        res = client.table(TABLE).update(payload).eq("id", card_id).execute()
        return bool(res.data)

    records = read_jsonl(CANDIDATES_FILE)
    found = False
    for record in records:
        if record.get("id") == card_id:
            record["is_published"] = published
            record["published_at"] = published_at if published else None
            found = True
    if found:
        rewrite_jsonl(CANDIDATES_FILE, records)
    return found


def delete_card(card_id: str) -> bool:
    """Discard a staged card. Returns False if it was not found."""
    if is_supabase_configured():
        client = get_supabase_client()
        if client is None:
            raise CardSaveError("Supabase configured but client unavailable")
        res = client.table(TABLE).delete().eq("id", card_id).execute()
        return bool(res.data)

    records = read_jsonl(CANDIDATES_FILE)
    remaining = [r for r in records if r.get("id") != card_id]
    if len(remaining) == len(records):
        return False
    rewrite_jsonl(CANDIDATES_FILE, remaining)
    return True


def list_staged_cards() -> List[dict]:
    """All cards awaiting review. For the authenticated reviewer tool only."""
    if is_supabase_configured():
        client = get_supabase_client()
        if client is None:
            raise CardSaveError("Supabase configured but client unavailable")
        res = (
            client.table(TABLE)
            .select("*")
            .eq("is_published", False)
            .order("created_at", desc=True)
            .limit(200)
            .execute()
        )
        return res.data or []

    return read_jsonl(CANDIDATES_FILE)
