import os
import json
import pytest
from app.lib.supabase_client import is_supabase_configured, get_supabase_client
from app.repository.content_repository import save_card, card_exists_by_tid, get_feed
from app.models.schemas import Category, ContentType

def test_supabase_client_placeholder_fallback():
    # Should detect placeholder configuration in dev env
    assert is_supabase_configured() is False
    assert get_supabase_client() is None

def test_repository_save_and_get_feed_fallback(tmp_path, monkeypatch):
    temp_staging = tmp_path / "staging"
    temp_staging.mkdir()
    candidates_file = temp_staging / "candidates.jsonl"

    monkeypatch.setattr("app.repository.content_repository.STAGING_DIR", str(temp_staging))
    monkeypatch.setattr("app.repository.content_repository.CANDIDATES_FILE", str(candidates_file))

    card_data = {
        "id": "test-card-999",
        "content_type": "judgment_summary",
        "category": "cyber",
        "title": "Is encryption legal?",
        "question": "Is encryption legal?",
        "direct_answer": "Yes, under IT Act.",
        "explanation": "Explanation here...",
        "source_tid": "777001",
        # Cards are only visible once a reviewer publishes them.
        "is_published": True,
        "published_at": "2026-07-24T00:00:00+00:00"
    }

    # 1. Save card via repository
    saved_id = save_card(card_data)
    assert saved_id == "test-card-999"
    assert candidates_file.exists()

    # 2. Check deduplication check
    assert card_exists_by_tid("777001") is True
    assert card_exists_by_tid("000000") is False

    # 3. Retrieve feed
    cards, next_cursor = get_feed(category=Category.cyber, limit=10)
    assert len(cards) == 1
    assert cards[0]["id"] == "test-card-999"
    assert cards[0]["category"] == "cyber"


def test_staged_card_is_not_in_public_feed(tmp_path, monkeypatch):
    """A card awaiting human review must never reach the feed."""
    temp_staging = tmp_path / "staging"
    temp_staging.mkdir()
    candidates_file = temp_staging / "candidates.jsonl"

    monkeypatch.setattr("app.repository.content_repository.STAGING_DIR", str(temp_staging))
    monkeypatch.setattr("app.repository.content_repository.CANDIDATES_FILE", str(candidates_file))

    save_card({
        "id": "staged-001",
        "content_type": "rights_explainer",
        "category": "posco",
        "title": "Child Protection & Safety Laws",
        "source_tid": "888001",
        "is_published": False,
        "published_at": None,
    })

    cards, _ = get_feed(content_type=ContentType.rights_explainer, limit=10)
    assert cards == []

    # ...and appears once a reviewer approves it.
    from app.repository.content_repository import set_published
    assert set_published("staged-001", True, "2026-07-25T00:00:00+00:00") is True

    cards, _ = get_feed(content_type=ContentType.rights_explainer, limit=10)
    assert [c["id"] for c in cards] == ["staged-001"]


def test_supabase_failure_does_not_leak_into_staging(tmp_path, monkeypatch):
    """A DB insert failure must not silently write into the public-feed file."""
    import app.repository.content_repository as repo

    temp_staging = tmp_path / "staging"
    temp_staging.mkdir()
    candidates_file = temp_staging / "candidates.jsonl"
    failed_file = temp_staging / "failed_saves.jsonl"

    monkeypatch.setattr(repo, "STAGING_DIR", str(temp_staging))
    monkeypatch.setattr(repo, "CANDIDATES_FILE", str(candidates_file))
    monkeypatch.setattr(repo, "FAILED_SAVES_FILE", str(failed_file))
    monkeypatch.setattr(repo, "is_supabase_configured", lambda: True)

    class BoomClient:
        def table(self, _name):
            raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(repo, "get_supabase_client", lambda: BoomClient())

    with pytest.raises(repo.CardSaveError):
        save_card({"id": "boom-001", "category": "cyber", "source_tid": "999001"})

    # The failure is captured for replay, not published.
    assert not candidates_file.exists()
    assert failed_file.exists()
    assert "boom-001" in failed_file.read_text(encoding="utf-8")
