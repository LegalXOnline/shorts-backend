import logging
from typing import List, Set
from app.models.schemas import SearchResult

logger = logging.getLogger(__name__)

NON_JUDGMENT_BLOCKLIST = {
    "law commission",
    "lok sabha", 
    "rajya sabha",
    "parliament",
    "gazette",
    "press information bureau",
}

def is_judgment_source(docsource: str) -> bool:
    """Check if docsource is from an actual court (not Law Commission, Lok Sabha, etc)."""
    if not docsource:
        return True # Default to true if not provided, though we can filter elsewhere
        
    docsource_lower = docsource.lower()
    for block_term in NON_JUDGMENT_BLOCKLIST:
        if block_term in docsource_lower:
            return False
            
    return True

def filter_search_results(
    results: List[SearchResult], 
    seen_tids: Set[str], 
    min_docsize: int = 100
) -> List[SearchResult]:
    """Apply cheap pre-filters. Returns survivors only.
    
    Filters:
    1. docsource must not match NON_JUDGMENT_BLOCKLIST
    2. tid must not be in seen_tids (dedup)
    3. docsize soft filter — warn but don't hard-reject if 0 (it's unreliable)
    """
    survivors = []
    
    for result in results:
        # Filter 1: Deduplication
        if result.tid in seen_tids:
            continue
            
        # Filter 2: Non-judgment blocklist
        if not is_judgment_source(result.docsource):
            continue
            
        # Filter 3: docsize soft check
        if result.docsize == 0:
            logger.warning("Result %s has docsize 0 — including anyway (docsize is unreliable)", result.tid)
        elif result.docsize < min_docsize:
            # We skip if it is greater than 0 but less than min_docsize
            continue
            
        survivors.append(result)
        
    return survivors
