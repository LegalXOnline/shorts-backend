import os
import json
import pytest
from unittest.mock import MagicMock
from app.models.schemas import Category, SearchResult, FetchedDocument, GateVerdict, CardDraft
from app.ingestion.pipeline import run_ingestion_pipeline, load_seen_tids, CANDIDATES_FILE, RUN_STATS_FILE

@pytest.mark.anyio
async def test_pipeline_orchestrator_mocked(tmp_path):
    # Setup temp directory for staging files during test
    temp_staging = tmp_path / "staging"
    temp_staging.mkdir()
    
    # Mock IndianKanoon client
    mock_ik = MagicMock()
    mock_ik.build_form_input.return_value = "cyber fraud doctypes: supremecourt"
    mock_ik.search.return_value = [
        SearchResult(tid="99001", title="State vs Fraudster", docsource="Supreme Court of India", docsize=5000)
    ]
    mock_ik.fetch_document.return_value = FetchedDocument(
        tid="99001",
        title="State vs Fraudster",
        doc_html="<html><body><p data-structure='Facts'>Cyber fraud facts...</p><p data-structure='Conclusion'>Accused convicted.</p></body></html>",
        docsource="Supreme Court of India"
    )

    # Mock Groq client
    mock_groq = MagicMock()
    
    # Mock AI Gate choice
    mock_gate_choice = MagicMock()
    mock_gate_choice.message.content = json.dumps({
        "card_worthy": True,
        "reasoning": "Valid cyber fraud case.",
        "is_final_judgment": True,
        "suggested_category": "cyber"
    })
    
    # Mock Card Generator choice
    mock_card_choice = MagicMock()
    mock_card_choice.message.content = json.dumps({
        "question": "Can a hacker be convicted without recovery of device?",
        "direct_answer": "Yes, if circumstantial digital evidence is proved.",
        "explanation": "The Supreme Court held that physical device recovery is not compulsory.",
        "suggested_questions": ["What is Section 66D?"]
    })

    # Side effect for chat.completions.create based on model parameter
    def mock_create(*args, **kwargs):
        model = kwargs.get("model", "")
        mock_resp = MagicMock()
        if "8b" in model:
            mock_resp.choices = [mock_gate_choice]
        else:
            mock_resp.choices = [mock_card_choice]
        return mock_resp

    mock_groq.chat.completions.create.side_effect = mock_create

    # Override staging file locations temporarily for test
    with pytest.MonkeyPatch.context() as m:
        m.setattr("app.ingestion.pipeline.STAGING_DIR", str(temp_staging))
        m.setattr("app.ingestion.pipeline.CANDIDATES_FILE", str(temp_staging / "candidates.jsonl"))
        m.setattr("app.ingestion.pipeline.RUN_STATS_FILE", str(temp_staging / "run_stats.jsonl"))
        m.setattr("app.repository.content_repository.STAGING_DIR", str(temp_staging))
        m.setattr("app.repository.content_repository.CANDIDATES_FILE", str(temp_staging / "candidates.jsonl"))
        m.setattr("app.ingestion.ai_gate._log_ai_gate_decision", lambda *a, **kw: None)

        summary = await run_ingestion_pipeline(
            category=Category.cyber,
            max_docs_to_fetch=1,
            groq_client=mock_groq,
            ik_client=mock_ik
        )

        assert summary["status"] == "success"
        assert summary["found_count"] == 1
        assert summary["docs_fetched"] == 1
        # The pipeline STAGES cards for review; it never publishes them.
        assert summary["cards_staged"] == 1
        assert "cards_published" not in summary

        # Check candidates.jsonl file was written correctly
        cand_file = temp_staging / "candidates.jsonl"
        assert cand_file.exists()
        lines = cand_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 1
        staged_card = json.loads(lines[0])
        assert staged_card["source_tid"] == "99001"
        assert staged_card["category"] == "cyber"
        assert staged_card["question"] == "Can a hacker be convicted without recovery of device?"
        assert staged_card["case_reference"] == "State vs Fraudster (Supreme Court of India)"
        # Judgment cards must be staged for human review, not auto-published.
        assert staged_card["is_published"] is False
        assert staged_card["published_at"] is None
