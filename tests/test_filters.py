from app.ingestion.filters import is_judgment_source, filter_search_results
from app.models.schemas import SearchResult

def test_is_judgment_source():
    # Valid sources
    assert is_judgment_source("Supreme Court of India")
    assert is_judgment_source("Delhi High Court")
    assert is_judgment_source("NCLAT")
    assert is_judgment_source("")  # defaults to True

    # Blocklisted sources (should return False)
    assert not is_judgment_source("Law Commission of India")
    assert not is_judgment_source("Lok Sabha debate transcript")
    assert not is_judgment_source("Rajya Sabha debates")
    assert not is_judgment_source("Parliament of India")
    assert not is_judgment_source("The Gazette of India")
    assert not is_judgment_source("Press Information Bureau")

def test_filter_search_results():
    results = [
        SearchResult(tid="1", title="Doc 1", docsource="Supreme Court of India", docsize=500),      # Keep
        SearchResult(tid="2", title="Doc 2", docsource="Supreme Court of India", docsize=50),       # Exclude (< min_docsize)
        SearchResult(tid="3", title="Doc 3", docsource="Supreme Court of India", docsize=0),        # Keep (unreliable docsize=0 check)
        SearchResult(tid="4", title="Doc 4", docsource="Law Commission", docsize=1000),             # Exclude (non-judgment)
        SearchResult(tid="5", title="Doc 5", docsource="Delhi High Court", docsize=200),            # Exclude (duplicate)
    ]
    
    seen_tids = {"5"}  # Doc 5 is already seen
    
    filtered = filter_search_results(results, seen_tids, min_docsize=100)
    
    assert len(filtered) == 2
    assert filtered[0].tid == "1"
    assert filtered[1].tid == "3"
