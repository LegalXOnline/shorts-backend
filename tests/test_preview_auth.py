"""Tests for the reviewer preview tool's access controls.

These endpoints publish and permanently delete content. They were previously
mounted unconditionally with no authentication of any kind.
"""
import base64
import importlib
import json

import pytest
from fastapi.testclient import TestClient


def _auth(username, password):
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _build_app(monkeypatch, **overrides):
    """Rebuild the app so settings-dependent router mounting is re-evaluated."""
    import app.config as config

    for key, value in overrides.items():
        monkeypatch.setattr(config.settings, key, value, raising=False)

    import app.main
    importlib.reload(app.main)
    return app.main.app


@pytest.fixture
def staged(tmp_path, monkeypatch):
    import app.repository.content_repository as repo

    candidates = tmp_path / "candidates.jsonl"
    card = {
        "id": "11111111-1111-4111-8111-111111111111",
        "content_type": "judgment_summary",
        "category": "cyber",
        "title": "Staged card",
        "is_published": False,
        "published_at": None,
    }
    candidates.write_text(json.dumps(card) + "\n", encoding="utf-8")
    monkeypatch.setattr(repo, "CANDIDATES_FILE", str(candidates))
    return card, candidates


def test_preview_not_mounted_in_production_without_credentials(monkeypatch):
    """Production must not expose the tool just because the flag is on."""
    app = _build_app(
        monkeypatch,
        environment="production",
        enable_preview_ui=True,
        reviewer_username="",
        reviewer_password="",
    )
    client = TestClient(app)
    assert client.get("/preview").status_code == 404
    assert client.get("/preview/data").status_code == 404
    assert client.post("/preview/approve/anything").status_code == 404


def test_preview_requires_credentials_when_configured(monkeypatch, staged):
    app = _build_app(
        monkeypatch,
        environment="production",
        enable_preview_ui=True,
        reviewer_username="reviewer",
        reviewer_password="a-long-enough-secret",
    )
    client = TestClient(app)
    card, _ = staged

    # No credentials
    assert client.get("/preview/data").status_code == 401
    assert client.post(f"/preview/approve/{card['id']}").status_code == 401

    # Wrong credentials
    bad = _auth("reviewer", "wrong-password-here")
    assert client.get("/preview/data", headers=bad).status_code == 401

    # Correct credentials
    good = _auth("reviewer", "a-long-enough-secret")
    assert client.get("/preview/data", headers=good).status_code == 200


def test_approve_publishes_and_reject_deletes(monkeypatch, staged):
    app = _build_app(
        monkeypatch,
        environment="production",
        enable_preview_ui=True,
        reviewer_username="reviewer",
        reviewer_password="a-long-enough-secret",
    )
    client = TestClient(app)
    card, candidates = staged
    good = _auth("reviewer", "a-long-enough-secret")

    # Approving sets is_published and stamps published_at.
    res = client.post(f"/preview/approve/{card['id']}", headers=good)
    assert res.status_code == 200
    stored = json.loads(candidates.read_text(encoding="utf-8").strip())
    assert stored["is_published"] is True
    assert stored["published_at"] is not None

    # Unknown ids are 404, not a silent success.
    assert client.post("/preview/approve/does-not-exist", headers=good).status_code == 404

    # Rejecting removes the card.
    res = client.post(f"/preview/reject/{card['id']}", headers=good)
    assert res.status_code == 200
    assert candidates.read_text(encoding="utf-8").strip() == ""


def test_preview_page_has_no_html_interpolation():
    """The template must not build card markup by string interpolation.

    Card content is LLM output over scraped HTML; the old template injected it
    straight into innerHTML, including into an onclick attribute and an href.
    """
    from app.api.preview import MINIMALIST_PREVIEW_TEMPLATE as tpl

    # Strip comments so the explanatory notes below don't match.
    code = "\n".join(
        line for line in tpl.splitlines() if not line.strip().startswith("//")
    )

    assert ".innerHTML" not in code
    assert "onclick=" not in code
    # Values must reach the DOM via textContent / explicit property assignment.
    assert "textContent" in tpl
    assert "safeHttpUrl" in tpl


def teardown_module(module):
    """Restore the default app module for other test files."""
    import app.main
    importlib.reload(app.main)
