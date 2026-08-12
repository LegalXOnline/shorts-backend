import logging
import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.config import settings
from app.rate_limit import limiter, PREVIEW_LIMIT
from app.repository.content_repository import (
    delete_card,
    list_staged_cards,
    set_published,
)

logger = logging.getLogger(__name__)

_basic = HTTPBasic(auto_error=False)


def require_reviewer(
    credentials: Optional[HTTPBasicCredentials] = Depends(_basic),
) -> str:
    """Gate every preview endpoint behind reviewer credentials.

    These endpoints publish and permanently delete content, so they were the
    single most exposed surface in the app: previously mounted in production
    with no authentication whatsoever. HTTP Basic is used so the browser
    attaches the credential to the page's own fetch() calls automatically.
    """
    if not settings.preview_credentials_set:
        if settings.preview_requires_auth:
            # app.main refuses to mount the router in this state; belt and braces.
            raise HTTPException(status_code=503, detail="Reviewer tool not configured")
        return "dev-unauthenticated"

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Reviewer credentials required",
            headers={"WWW-Authenticate": 'Basic realm="LegalX Reviewer"'},
        )

    # compare_digest on both fields, and always both, to avoid leaking which
    # half was wrong via timing. Compare as bytes: compare_digest raises
    # TypeError on non-ASCII str, which a client could trigger for a 500.
    user_ok = secrets.compare_digest(
        credentials.username.encode("utf-8"), settings.reviewer_username.encode("utf-8")
    )
    pass_ok = secrets.compare_digest(
        credentials.password.encode("utf-8"), settings.reviewer_password.encode("utf-8")
    )
    if not (user_ok and pass_ok):
        logger.warning("Rejected reviewer login for username=%r", credentials.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid reviewer credentials",
            headers={"WWW-Authenticate": 'Basic realm="LegalX Reviewer"'},
        )

    return credentials.username


router = APIRouter(tags=["preview"], dependencies=[Depends(require_reviewer)])

MINIMALIST_PREVIEW_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>LegalX Shorts — Clean Minimalist Previewer</title>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Plus Jakarta Sans', -apple-system, sans-serif; }
        body { background: #f8fafc; color: #0f172a; display: flex; flex-direction: column; align-items: center; min-height: 100vh; padding: 30px 16px; }

        .header { text-align: center; margin-bottom: 24px; }
        .header h1 { font-size: 22px; font-weight: 800; color: #0f172a; letter-spacing: -0.5px; }
        .header p { color: #64748b; font-size: 13px; margin-top: 4px; font-weight: 500; }

        .tabs { display: flex; background: #e2e8f0; padding: 4px; border-radius: 30px; margin-bottom: 24px; }
        .tab-btn { background: transparent; color: #64748b; border: none; padding: 10px 22px; border-radius: 24px; font-weight: 600; font-size: 13px; cursor: pointer; transition: all 0.2s ease; }
        .tab-btn.active { background: #ffffff; color: #0f172a; box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08); }

        /* Showcase Device Frame — Bigger & Spacious */
        .phone-frame { width: 100%; max-width: 480px; background: #ffffff; border: 1px solid #e2e8f0; border-radius: 36px; padding: 34px 30px; box-shadow: 0 25px 50px -12px rgba(15, 23, 42, 0.12); min-height: 680px; display: flex; flex-direction: column; justify-content: space-between; }

        .badge { display: inline-flex; align-items: center; padding: 6px 14px; border-radius: 20px; font-size: 12px; font-weight: 700; letter-spacing: 0.4px; text-transform: uppercase; margin-bottom: 18px; }
        .badge-judgments { background: #eff6ff; color: #2563eb; }
        .badge-rights { background: #f3e8ff; color: #7e22ce; }

        .card-title { font-size: 22px; font-weight: 800; line-height: 1.35; color: #0f172a; margin-bottom: 18px; letter-spacing: -0.4px; }
        .card-answer-box { background: #f1f5f9; border-radius: 14px; padding: 16px 18px; font-size: 15px; line-height: 1.55; color: #1e293b; font-weight: 600; margin-bottom: 18px; }
        .answer-label { color: #2563eb; font-weight: 800; }

        .card-body { font-size: 15.5px; line-height: 1.68; color: #334155; font-weight: 450; margin-bottom: 22px; max-height: 320px; overflow-y: auto; white-space: pre-wrap; }

        .ref-box { background: #fafafa; border: 1px solid #f1f5f9; border-radius: 14px; padding: 14px 16px; margin-bottom: 20px; }
        .ref-label { font-size: 11.5px; text-transform: uppercase; letter-spacing: 0.5px; color: #94a3b8; font-weight: 700; margin-bottom: 4px; }
        .ref-text { font-size: 13.5px; color: #334155; font-weight: 600; }
        .source-link { color: #2563eb; text-decoration: none; font-size: 13px; font-weight: 600; display: inline-block; margin-top: 6px; }

        .action-bar { display: flex; gap: 12px; }
        .action-btn { flex: 1; padding: 14px; border: none; border-radius: 14px; font-weight: 700; font-size: 14px; cursor: pointer; transition: transform 0.1s ease; text-align: center; }
        .action-btn:active { transform: scale(0.98); }
        .btn-approve { background: #10b981; color: #ffffff; }
        .btn-reject { background: #f1f5f9; color: #64748b; }
        .btn-reject:hover { background: #fee2e2; color: #ef4444; }

        .footer-nav { display: flex; justify-content: space-between; align-items: center; width: 100%; max-width: 480px; margin-top: 20px; }
        .nav-link { background: none; border: none; color: #64748b; font-weight: 600; font-size: 13px; cursor: pointer; }
        .nav-link:disabled { opacity: 0.3; cursor: not-allowed; }
        .counter-text { font-size: 12px; color: #94a3b8; font-weight: 600; }

        .empty-view { text-align: center; padding: 60px 20px; color: #94a3b8; font-size: 14px; font-weight: 500; }
    </style>
</head>
<body>

<div class="header">
    <h1>LegalX Shorts</h1>
    <p>Human Reviewer &amp; Founder Visualizer</p>
</div>

<div class="tabs">
    <button class="tab-btn active" id="tab-judgments">🏛️ Judgments Reel</button>
    <button class="tab-btn" id="tab-rights">📜 Know Your Rights</button>
</div>

<div class="phone-frame" id="card-frame">
    <div class="empty-view">Loading cards...</div>
</div>

<div class="footer-nav">
    <button class="nav-link" id="prev-btn" disabled>← Previous</button>
    <span class="counter-text" id="counter">0 / 0</span>
    <button class="nav-link" id="next-btn">Next →</button>
</div>

<script>
    // All card content is LLM-generated from scraped third-party HTML, so it is
    // treated as untrusted: every value below is inserted with textContent or a
    // scheme-checked href. The previous version built this markup by string
    // interpolation into innerHTML, which was a stored XSS sink.
    let currentTab = 'judgments';
    let cards = [];
    let currentIndex = 0;

    const $ = (id) => document.getElementById(id);

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = String(text);
        return node;
    }

    function safeHttpUrl(value) {
        // Blocks javascript:, data:, and other non-navigational schemes.
        try {
            const parsed = new URL(String(value), window.location.origin);
            return (parsed.protocol === 'https:' || parsed.protocol === 'http:') ? parsed.href : null;
        } catch (e) {
            return null;
        }
    }

    async function fetchCards() {
        try {
            const res = await fetch('/preview/data', { credentials: 'same-origin' });
            if (!res.ok) throw new Error('HTTP ' + res.status);
            const allCards = await res.json();
            const wanted = currentTab === 'judgments' ? 'judgment_summary' : 'rights_explainer';
            cards = allCards.filter(c => c.content_type === wanted);
            currentIndex = 0;
            renderCard();
        } catch (err) {
            const frame = $('card-frame');
            frame.replaceChildren(el('div', 'empty-view', 'Unable to load cards. Make sure the backend is running and you are signed in.'));
        }
    }

    function switchTab(tab) {
        currentTab = tab;
        $('tab-judgments').classList.toggle('active', tab === 'judgments');
        $('tab-rights').classList.toggle('active', tab === 'rights');
        fetchCards();
    }

    function renderCard() {
        const frame = $('card-frame');
        const counter = $('counter');
        const prevBtn = $('prev-btn');
        const nextBtn = $('next-btn');

        if (!cards || cards.length === 0) {
            frame.replaceChildren(el('div', 'empty-view', `No staged ${currentTab} cards available. Run a pipeline runner script to stage new cards.`));
            counter.textContent = '0 / 0';
            prevBtn.disabled = true;
            nextBtn.disabled = true;
            return;
        }

        const c = cards[currentIndex];
        const isRights = c.content_type === 'rights_explainer';
        const category = (c.category || 'general').toUpperCase();

        const top = el('div');

        const badge = el('div', 'badge ' + (isRights ? 'badge-rights' : 'badge-judgments'),
            (isRights ? '📜 ' + category + ' LAW' : '🏛️ ' + category + ' RULING'));
        top.appendChild(badge);

        top.appendChild(el('h2', 'card-title', c.title || c.question || '(untitled)'));

        if (!isRights && c.direct_answer) {
            const box = el('div', 'card-answer-box');
            box.appendChild(el('span', 'answer-label', 'Direct Answer:'));
            box.appendChild(document.createTextNode(' ' + c.direct_answer));
            top.appendChild(box);
        }

        top.appendChild(el('div', 'card-body', c.explanation || c.card_text || ''));

        const bottom = el('div');
        const refBox = el('div', 'ref-box');
        refBox.appendChild(el('div', 'ref-label', 'Legal Reference'));
        refBox.appendChild(el('div', 'ref-text', c.case_reference || '—'));

        const href = c.source_url ? safeHttpUrl(c.source_url) : null;
        if (href) {
            const link = el('a', 'source-link', 'View Source on IndianKanoon ↗');
            link.href = href;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            refBox.appendChild(link);
        }
        bottom.appendChild(refBox);

        const bar = el('div', 'action-bar');
        const approve = el('button', 'action-btn btn-approve', 'Approve & Publish');
        approve.addEventListener('click', () => approveCard(c.id));
        const reject = el('button', 'action-btn btn-reject', 'Discard');
        reject.addEventListener('click', () => rejectCard(c.id));
        bar.appendChild(approve);
        bar.appendChild(reject);
        bottom.appendChild(bar);

        frame.replaceChildren(top, bottom);
        counter.textContent = `${currentIndex + 1} of ${cards.length}`;
        prevBtn.disabled = currentIndex === 0;
        nextBtn.disabled = currentIndex === cards.length - 1;
    }

    async function review(action, id) {
        try {
            const res = await fetch(`/preview/${action}/${encodeURIComponent(id)}`, {
                method: 'POST',
                credentials: 'same-origin',
            });
            if (res.ok) fetchCards();
        } catch (e) {
            /* transient network error — the next refresh will resync */
        }
    }

    const approveCard = (id) => review('approve', id);
    const rejectCard = (id) => review('reject', id);

    $('tab-judgments').addEventListener('click', () => switchTab('judgments'));
    $('tab-rights').addEventListener('click', () => switchTab('rights'));
    $('prev-btn').addEventListener('click', () => {
        if (currentIndex > 0) { currentIndex--; renderCard(); }
    });
    $('next-btn').addEventListener('click', () => {
        if (currentIndex < cards.length - 1) { currentIndex++; renderCard(); }
    });

    fetchCards();
</script>

</body>
</html>
"""


@router.get("/preview", response_class=HTMLResponse)
@limiter.limit(PREVIEW_LIMIT)
def preview_page(request: Request):
    """Minimalist, humanized visualizer for Founder & Reviewer preview."""
    return HTMLResponse(content=MINIMALIST_PREVIEW_TEMPLATE)


@router.get("/preview/data")
@limiter.limit(PREVIEW_LIMIT)
def preview_data(request: Request):
    """Return staged candidate cards for the visualizer (reviewers only)."""
    return list_staged_cards()


@router.post("/preview/approve/{card_id}")
@limiter.limit(PREVIEW_LIMIT)
def approve_staged_card(request: Request, card_id: str, reviewer: str = Depends(require_reviewer)):
    """Publish a staged card. This is the human review gate — audit-logged."""
    now_iso = datetime.now(timezone.utc).isoformat()
    if not set_published(card_id, True, now_iso):
        raise HTTPException(status_code=404, detail="Card not found")
    logger.info("Card %s APPROVED and published by reviewer %r", card_id, reviewer)
    return {"status": "success", "approved_id": card_id}


@router.post("/preview/reject/{card_id}")
@limiter.limit(PREVIEW_LIMIT)
def reject_staged_card(request: Request, card_id: str, reviewer: str = Depends(require_reviewer)):
    """Discard a staged card. Destructive — audit-logged."""
    if not delete_card(card_id):
        raise HTTPException(status_code=404, detail="Card not found")
    logger.info("Card %s REJECTED and deleted by reviewer %r", card_id, reviewer)
    return {"status": "success", "rejected_id": card_id}
