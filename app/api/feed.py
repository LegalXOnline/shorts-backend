import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Request
from app.models.schemas import Category, ContentType, FeedCard, FeedResponse
from app.rate_limit import limiter, FEED_LIMIT
from app.repository.content_repository import get_feed as repo_get_feed

logger = logging.getLogger(__name__)

router = APIRouter(tags=["feed"])


@router.get("/feed", response_model=FeedResponse)
# S-4: the rate limit is now actually enforced. The decorator was previously
# missing entirely — only a comment claimed 30 req/min existed.
@limiter.limit(FEED_LIMIT)
async def get_feed(
    request: Request,
    category: Optional[Category] = None,
    content_type: ContentType = ContentType.judgment_summary,
    # Opaque base64 keyset cursor of (published_at, id) — see
    # content_repository.encode_cursor. Bounded to reject oversized input.
    cursor: Optional[str] = Query(default=None, max_length=200),
    limit: int = Query(default=20, ge=1, le=50, description="Max cards to return (1-50)")
) -> FeedResponse:
    """GET /feed endpoint for flashcard feed.

    Serves published Q&A flashcards with enum validation, keyset pagination,
    and server-side limit capping. Uses Supabase if configured, with JSONL
    staging as the local development backend.
    """
    try:
        page_cards, next_cursor = repo_get_feed(
            category=category,
            content_type=content_type,
            cursor=cursor,
            limit=limit
        )
    except Exception:
        # Log the detail server-side; never leak backend errors to clients.
        logger.exception("Feed retrieval failed")
        raise HTTPException(status_code=503, detail="Feed temporarily unavailable")

    feed_cards = [
        FeedCard(
            id=c.get("id", ""),
            content_type=c.get("content_type", content_type.value),
            category=c.get("category", ""),
            title=c.get("title", c.get("question", "")),
            card_text=c.get("card_text", f"Q: {c.get('question','')}\n\nA: {c.get('direct_answer','')}\n\n{c.get('explanation','')}"),
            source_url=c.get("source_url"),
            published_at=c.get("published_at")
        )
        for c in page_cards
    ]

    return FeedResponse(cards=feed_cards, next_cursor=next_cursor)
