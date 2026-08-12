from enum import Enum
from pydantic import BaseModel, Field

class Category(str, Enum):
    cyber = "cyber"
    traffic = "traffic"
    posco = "posco"
    consumer = "consumer"
    cheque_ni_act = "cheque_ni_act"

class ContentType(str, Enum):
    judgment_summary = "judgment_summary"
    rights_explainer = "rights_explainer"

class SearchResult(BaseModel):
    """A single result from IndianKanoon search."""
    tid: str
    title: str
    headline: str = ""
    docsource: str = ""
    docsize: int = 0

class FetchedDocument(BaseModel):
    """Full document fetched from IndianKanoon."""
    tid: str
    title: str
    doc_html: str  # raw HTML content
    docsource: str = ""

class StructuredSections(BaseModel):
    """Parsed judgment sections from HTML data-structure attributes."""
    facts: str = ""
    issue: str = ""
    conclusion: str = ""
    arguments: str = ""
    order: str = ""
    
    def has_substance(self) -> bool:
        """A judgment has substance if it has at least Facts and Conclusion."""
        return bool(self.facts.strip() and self.conclusion.strip())
    
    def to_dict(self) -> dict[str, str]:
        """Return only non-empty sections as a dict with capitalized keys."""
        sections = {}
        if self.facts: sections["Facts"] = self.facts
        if self.issue: sections["Issue"] = self.issue
        if self.conclusion: sections["Conclusion"] = self.conclusion
        if self.arguments: sections["Arguments"] = self.arguments
        if self.order: sections["Order"] = self.order
        return sections

class SanitizedContent(BaseModel):
    """Sanitized structured text ready for LLM prompts."""
    sections: dict[str, str]
    content_hash: str  # SHA-256 of the sanitized content
    xml_prompt_block: str  # pre-formatted XML for prompt injection

class GateVerdict(BaseModel):
    """AI gate output — is this judgment card-worthy?"""
    card_worthy: bool
    reasoning: str
    is_final_judgment: bool
    suggested_category: str

class CardDraft(BaseModel):
    """Q&A styled flashcard draft.

    Limits are deliberately a little above the targets stated in the prompts
    (120 / 120 / 600) so a slightly long generation is kept rather than thrown
    away, but close enough that a card still fits one mobile screen. They used
    to be 200/200/1200 — up to 2x the designed layout — so overruns rendered
    badly instead of being caught.
    """
    question: str = Field(..., min_length=10, max_length=140, description="Practical scenario-based question (target 120 chars)")
    direct_answer: str = Field(..., min_length=5, max_length=140, description="Immediate 1-sentence answer (target 120 chars)")
    explanation: str = Field(..., min_length=50, max_length=750, description="Plain-language breakdown (target ~600 chars)")
    case_reference: str = Field(..., max_length=300, description="Case citation reference string")
    suggested_questions: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="2-3 short follow-up questions for Ask AI",
    )

class FeedCard(BaseModel):
    """A card as returned by the GET /feed endpoint."""
    id: str
    content_type: str
    category: str
    title: str
    card_text: str
    source_url: str | None = None
    published_at: str | None = None

class FeedResponse(BaseModel):
    """Response shape for GET /feed."""
    cards: list[FeedCard]
    next_cursor: str | None = None
