import json
import pytest
from unittest.mock import MagicMock
from app.models.schemas import Category, SearchResult, FetchedDocument, ContentType
from app.ingestion.rights_summarizer import summarize_rights_section, RightsSummaryDraft
from app.ingestion.rights_pipeline import run_rights_pipeline, extract_section_text

def test_extract_section_text():
    html = "<html><body><h2>Title</h2><script>alert(1)</script><p>Section 19 text here.</p></body></html>"
    text = extract_section_text(html)
    assert "Title" in text
    assert "Section 19 text here." in text
    assert "alert(1)" not in text

@pytest.mark.anyio
async def test_rights_summarizer_success():
    mock_groq = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps({
        "title": "Mandatory Obligation to Report POCSO Offences",
        "summary": "Under Section 19 of the POCSO Act, any person having knowledge of a sexual offence against a child must report it to the police immediately. Failure to report is a punishable offence.",
        "statute_reference": "Section 19, POCSO Act, 2012"
    })
    mock_groq.chat.completions.create.return_value.choices = [mock_choice]

    draft = await summarize_rights_section(
        section_title="Section 19",
        section_text="Notwithstanding anything contained in CrPC...",
        act_title="POCSO Act",
        category=Category.posco,
        groq_client=mock_groq
    )

    assert draft is not None
    assert draft.title == "Mandatory Obligation to Report POCSO Offences"
    assert "Section 19, POCSO Act, 2012" in draft.statute_reference

@pytest.mark.anyio
async def test_rights_pipeline_orchestrator_mocked(tmp_path, monkeypatch):
    temp_staging = tmp_path / "staging"
    temp_staging.mkdir()
    candidates_file = temp_staging / "candidates.jsonl"

    monkeypatch.setattr("app.repository.content_repository.STAGING_DIR", str(temp_staging))
    monkeypatch.setattr("app.repository.content_repository.CANDIDATES_FILE", str(candidates_file))

    # Mock IndianKanoon client
    mock_ik = MagicMock()
    mock_ik.search.return_value = [
        SearchResult(tid="25516219", title="Section 19 in The POCSO Act", docsource="Union of India - Section", docsize=1000)
    ]
    mock_ik.fetch_document.return_value = FetchedDocument(
        tid="25516219",
        title="Section 19 in The POCSO Act",
        doc_html="<section><h3>19. Reporting of offences</h3><p>Any person having knowledge shall report...</p></section>",
        docsource="Union of India - Section"
    )

    # Mock Groq client
    mock_groq = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps({
        "title": "Mandatory Obligation to Report POCSO Offences",
        "summary": "Under Section 19 of the POCSO Act, any person having knowledge of a sexual offence against a child must report it to the police immediately.",
        "statute_reference": "Section 19, POCSO Act, 2012"
    })
    mock_groq.chat.completions.create.return_value.choices = [mock_choice]

    summary = await run_rights_pipeline(
        act_name="Protection of Children from Sexual Offences Act",
        category=Category.posco,
        max_sections=1,
        groq_client=mock_groq,
        ik_client=mock_ik
    )

    assert summary["status"] == "success"
    assert summary["sections_fetched"] == 1
    assert summary["cards_staged"] == 1

    assert candidates_file.exists()
    lines = candidates_file.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1

    staged_card = json.loads(lines[0])
    assert staged_card["content_type"] == ContentType.rights_explainer.value
    assert staged_card["category"] == "posco"
    assert staged_card["is_published"] is False
