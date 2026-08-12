from app.models.schemas import Category, ContentType, SearchResult, StructuredSections

def test_enums():
    assert Category.cyber == "cyber"
    assert ContentType.judgment_summary == "judgment_summary"

def test_search_result_validation():
    res = SearchResult(tid="123", title="Test Judgment", docsource="Delhi High Court", docsize=500)
    assert res.tid == "123"
    assert res.docsize == 500

def test_structured_sections():
    # Empty sections
    empty = StructuredSections()
    assert not empty.has_substance()
    assert empty.to_dict() == {}

    # Partial sections
    partial = StructuredSections(facts="Some facts")
    assert not partial.has_substance()
    assert partial.to_dict() == {"Facts": "Some facts"}

    # Full substance
    substantive = StructuredSections(facts="Some facts", conclusion="Final conclusion", arguments="Some arguments")
    assert substantive.has_substance()
    assert substantive.to_dict() == {
        "Facts": "Some facts",
        "Conclusion": "Final conclusion",
        "Arguments": "Some arguments"
    }
