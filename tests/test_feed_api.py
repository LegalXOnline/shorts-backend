import json
import uuid
import pytest
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def _card(card_id, category, published, published_at, content_type="judgment_summary"):
    return {
        "id": card_id,
        "content_type": content_type,
        "category": category,
        "title": f"Title for {card_id}",
        "question": f"Question for {card_id}?",
        "direct_answer": "Yes.",
        "explanation": "Explanation here...",
        "is_published": published,
        "published_at": published_at,
    }


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


def test_security_headers_present():
    response = client.get("/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_feed_endpoint_success(tmp_path, monkeypatch):
    candidates_file = tmp_path / "candidates.jsonl"

    card1 = _card(str(uuid.uuid4()), "cyber", True, "2026-07-21T00:00:00+00:00")
    card2 = _card(str(uuid.uuid4()), "traffic", True, "2026-07-21T01:00:00+00:00")

    candidates_file.write_text(
        json.dumps(card1) + "\n" + json.dumps(card2) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr("app.repository.content_repository.CANDIDATES_FILE", str(candidates_file))

    # 1. Fetch all published cards
    response = client.get("/feed")
    assert response.status_code == 200
    assert len(response.json()["cards"]) == 2

    # 2. Filter by category=cyber
    response = client.get("/feed?category=cyber")
    assert response.status_code == 200
    data = response.json()
    assert len(data["cards"]) == 1
    assert data["cards"][0]["category"] == "cyber"
    assert data["cards"][0]["id"] == card1["id"]


def test_feed_never_serves_unpublished_cards(tmp_path, monkeypatch):
    """Regression test for the human-review bypass.

    The JSONL feed path used to filter only on content_type and category, so
    cards staged for human review — and cards with no is_published field at
    all — were served to the public feed.
    """
    candidates_file = tmp_path / "candidates.jsonl"

    published = _card(str(uuid.uuid4()), "cyber", True, "2026-07-21T00:00:00+00:00")
    staged = _card(str(uuid.uuid4()), "cyber", False, None)
    # A judgment card as the pipeline used to write it: no is_published key.
    legacy = _card(str(uuid.uuid4()), "cyber", True, "2026-07-21T02:00:00+00:00")
    del legacy["is_published"]

    candidates_file.write_text(
        "\n".join(json.dumps(c) for c in (published, staged, legacy)) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.repository.content_repository.CANDIDATES_FILE", str(candidates_file))

    response = client.get("/feed")
    assert response.status_code == 200
    ids = [c["id"] for c in response.json()["cards"]]
    assert ids == [published["id"]]
    assert staged["id"] not in ids
    assert legacy["id"] not in ids


def test_feed_cursor_pagination_advances(tmp_path, monkeypatch):
    """Paginating with next_cursor must move forward, not repeat page 1."""
    candidates_file = tmp_path / "candidates.jsonl"

    cards = [
        _card(str(uuid.uuid4()), "cyber", True, f"2026-07-{20 + i:02d}T00:00:00+00:00")
        for i in range(5)
    ]
    candidates_file.write_text(
        "\n".join(json.dumps(c) for c in cards) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr("app.repository.content_repository.CANDIDATES_FILE", str(candidates_file))

    seen = []
    cursor = None
    for _ in range(5):
        url = "/feed?limit=2" + (f"&cursor={cursor}" if cursor else "")
        data = client.get(url).json()
        page_ids = [c["id"] for c in data["cards"]]
        assert not (set(page_ids) & set(seen)), "cursor returned already-seen cards"
        seen.extend(page_ids)
        cursor = data["next_cursor"]
        if not cursor:
            break

    assert cursor is None
    assert len(seen) == 5
    assert len(set(seen)) == 5


def test_feed_rejects_garbage_cursor(tmp_path, monkeypatch):
    """A malformed cursor degrades to page 1 rather than erroring or injecting."""
    candidates_file = tmp_path / "candidates.jsonl"
    card = _card(str(uuid.uuid4()), "cyber", True, "2026-07-21T00:00:00+00:00")
    candidates_file.write_text(json.dumps(card) + "\n", encoding="utf-8")
    monkeypatch.setattr("app.repository.content_repository.CANDIDATES_FILE", str(candidates_file))

    for bad in ("not-base64!!", "cHVibGlzaGVkX2F0", "*" * 40):
        response = client.get(f"/feed?cursor={bad}")
        assert response.status_code == 200
        assert len(response.json()["cards"]) == 1


def test_feed_enum_validation():
    # Invalid category should be rejected by FastAPI Pydantic enum validation with 422
    response = client.get("/feed?category=invalid_category")
    assert response.status_code == 422


def test_feed_limit_clamping():
    # Limit > 50 should be rejected by Query(le=50) validation with 422
    response = client.get("/feed?limit=999")
    assert response.status_code == 422


def test_oversized_cursor_rejected():
    response = client.get("/feed?cursor=" + "a" * 500)
    assert response.status_code == 422
