from bs4 import BeautifulSoup
from app.models.schemas import StructuredSections

# Thresholds for the positional fallback split (see parse_structural_sections).
MIN_FALLBACK_PARAGRAPHS = 6
MIN_FALLBACK_CHARS = 1500

def parse_structural_sections(doc_html: str) -> StructuredSections:
    """Parse IndianKanoon HTML text to extract structured sections based on data-structure tags.
    
    IndianKanoon marks up paragraphs with data-structure attributes, e.g.:
    <p data-structure="Facts">...</p>
    <p data-structure="Issue">...</p>
    <p data-structure="Conclusion">...</p>
    """
    if not doc_html:
        return StructuredSections()

    soup = BeautifulSoup(doc_html, "html.parser")
    
    section_texts: dict[str, list[str]] = {
        "facts": [],
        "issue": [],
        "conclusion": [],
        "arguments": [],
        "order": []
    }
    
    # 1. Look for elements with data-structure attributes
    elements = soup.find_all(attrs={"data-structure": True})
    for elem in elements:
        attr_val = str(elem.get("data-structure", "")).strip().lower()
        text = elem.get_text(separator=" ", strip=True)
        if not text:
            continue
            
        if "fact" in attr_val:
            section_texts["facts"].append(text)
        elif "issue" in attr_val or "question" in attr_val:
            section_texts["issue"].append(text)
        elif "conclusion" in attr_val or "holding" in attr_val or "verdict" in attr_val:
            section_texts["conclusion"].append(text)
        elif "arg" in attr_val or "submission" in attr_val:
            section_texts["arguments"].append(text)
        elif "order" in attr_val or "decree" in attr_val or "judgment" in attr_val:
            section_texts["order"].append(text)
            
    # 2. Fallback heuristic if no data-structure attributes were found.
    #
    # This split is positional, not semantic — the second half of a document is
    # not necessarily its holding. It only runs when the document is long enough
    # that the halves are likely to be substantive. Previously ANY two
    # paragraphs over 20 chars produced non-empty Facts AND Conclusion, which
    # made has_substance() return True for thin procedural orders — exactly the
    # documents the substance gate exists to reject — and sent them on to be
    # summarised as if they were reasoned judgments.
    if not any(section_texts.values()):
        all_paragraphs = [p.get_text(separator=" ", strip=True) for p in soup.find_all(["p", "blockquote", "pre"])]
        all_paragraphs = [p for p in all_paragraphs if len(p) > 20]

        total_chars = sum(len(p) for p in all_paragraphs)
        if len(all_paragraphs) >= MIN_FALLBACK_PARAGRAPHS and total_chars >= MIN_FALLBACK_CHARS:
            midpoint = len(all_paragraphs) // 2
            section_texts["facts"] = all_paragraphs[:midpoint]
            section_texts["conclusion"] = all_paragraphs[midpoint:]
        elif all_paragraphs:
            # Too thin to infer a holding from position. Populate Facts only, so
            # has_substance() correctly rejects the document.
            section_texts["facts"] = all_paragraphs

    return StructuredSections(
        facts="\n\n".join(section_texts["facts"]),
        issue="\n\n".join(section_texts["issue"]),
        conclusion="\n\n".join(section_texts["conclusion"]),
        arguments="\n\n".join(section_texts["arguments"]),
        order="\n\n".join(section_texts["order"])
    )
