"""Shared test fixtures.

Critically, this isolates the staging directory for every test. The suite used
to append to the real staging/ai_gate_log.jsonl on each run (test_phase3's AI
gate test writes a log entry), which polluted production dedup data with mock
records — those fake TIDs would then suppress real documents from ingestion.
"""
import pytest


@pytest.fixture(autouse=True)
def isolate_staging(tmp_path, monkeypatch):
    """Point all staging paths at a per-test temp directory."""
    import app.repository.content_repository as repo
    import app.ingestion.pipeline as pipeline
    import app.ingestion.rights_pipeline as rights_pipeline

    # Deliberately not named "staging": several tests create tmp_path/"staging"
    # themselves and would collide with this default.
    staging = tmp_path / "_default_staging"
    staging.mkdir(exist_ok=True)

    monkeypatch.setattr(repo, "STAGING_DIR", str(staging))
    monkeypatch.setattr(repo, "CANDIDATES_FILE", str(staging / "candidates.jsonl"))
    monkeypatch.setattr(repo, "AI_GATE_LOG_FILE", str(staging / "ai_gate_log.jsonl"))
    monkeypatch.setattr(repo, "FAILED_SAVES_FILE", str(staging / "failed_saves.jsonl"))

    for module in (pipeline, rights_pipeline):
        monkeypatch.setattr(module, "STAGING_DIR", str(staging), raising=False)
        monkeypatch.setattr(module, "RUN_STATS_FILE", str(staging / "run_stats.jsonl"), raising=False)
        if hasattr(module, "CANDIDATES_FILE"):
            monkeypatch.setattr(module, "CANDIDATES_FILE", str(staging / "candidates.jsonl"))

    return staging


@pytest.fixture
def anyio_backend():
    """Run @pytest.mark.anyio tests on asyncio only (no trio dependency)."""
    return "asyncio"
