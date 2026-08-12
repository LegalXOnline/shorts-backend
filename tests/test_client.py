import pytest
from unittest.mock import patch, MagicMock
from app.ingestion.indiankanoon_client import IndianKanoonClient, BudgetExhaustedError
from app.models.schemas import SearchResult, FetchedDocument

def test_build_form_input():
    client = IndianKanoonClient(token="fake_token")
    
    # Test simple topic
    form_input = client.build_form_input("cyber fraud")
    assert form_input == "cyber fraud doctypes: supremecourt"
    
    # Test topic with custom doctypes and dates
    form_input_custom = client.build_form_input(
        topic="cheque bounce",
        doctypes="supremecourt",
        from_date="1-6-2026",
        to_date="17-7-2026"
    )
    assert form_input_custom == "cheque bounce doctypes: supremecourt fromdate: 1-6-2026 todate: 17-7-2026"

def test_budget_exhausted():
    # Set limit to 2 for testing
    client = IndianKanoonClient(token="fake_token", dev_mode=True, max_dev_calls=2)
    
    # First track call should work
    client._track_call()
    assert client._call_count == 1
    
    # Second track call should work
    client._track_call()
    assert client._call_count == 2
    
    # Third track call should raise error BEFORE incrementing
    with pytest.raises(BudgetExhaustedError) as exc_info:
        client._track_call()
    
    assert "Development budget exceeded" in str(exc_info.value)
    # The count should remain at 2 because the error was raised before incrementing
    assert client._call_count == 2

@patch("requests.post")
def test_search_success(mock_post):
    # Mock search response json
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "docs": [
            {"tid": "1001", "title": "State vs John", "headline": "cyber crime case", "docsource": "Supreme Court of India", "docsize": 1500},
            {"tid": "1002", "title": "Doe vs Smith", "headline": "civil dispute", "docsource": "Delhi High Court", "docsize": 0}
        ]
    }
    mock_post.return_value = mock_response
    
    client = IndianKanoonClient(token="fake_token")
    results = client.search("cyber crime")
    
    assert len(results) == 2
    assert results[0].tid == "1001"
    assert results[0].title == "State vs John"
    assert results[0].docsize == 1500
    assert results[1].tid == "1002"
    assert results[1].docsize == 0
    
    # Verify post was called correctly — including the S-2 timeout fix
    mock_post.assert_called_once_with(
        "https://api.indiankanoon.org/search/",
        headers={"Authorization": "Token fake_token"},
        data={"formInput": "cyber crime", "pagenum": 0},
        timeout=30
    )

@patch("requests.post")
def test_fetch_document_success(mock_post):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "title": "State vs John",
        "doc": "<html><body>Facts of the case...</body></html>",
        "docsource": "Supreme Court of India"
    }
    mock_post.return_value = mock_response
    
    client = IndianKanoonClient(token="fake_token")
    doc = client.fetch_document("1001")
    
    assert doc.tid == "1001"
    assert doc.title == "State vs John"
    assert "Facts of the case" in doc.doc_html
    assert doc.docsource == "Supreme Court of India"
    
    # Verify post was called correctly — including the S-2 timeout fix
    mock_post.assert_called_once_with(
        "https://api.indiankanoon.org/doc/1001/",
        headers={"Authorization": "Token fake_token"},
        data=None,
        timeout=30
    )


def test_fetch_document_rejects_non_numeric_tid():
    """tid is interpolated into the request path, so it must be a plain id."""
    client = IndianKanoonClient(token="fake_token")
    for bad in ("../../admin", "1001/../x", "abc", ""):
        with pytest.raises(ValueError):
            client.fetch_document(bad)


def test_token_is_required():
    with pytest.raises(ValueError):
        IndianKanoonClient(token="")


def test_production_budget_is_enforced():
    """The cap used to apply only in dev_mode, leaving production uncapped."""
    client = IndianKanoonClient(token="fake_token", dev_mode=False, max_calls=2)
    client._track_call()
    client._track_call()
    with pytest.raises(BudgetExhaustedError) as exc_info:
        client._track_call()
    assert "Production budget exceeded" in str(exc_info.value)
    assert client._call_count == 2
