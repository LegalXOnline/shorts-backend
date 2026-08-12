import re
import html
import json
import logging
import hashlib
import unicodedata
from app.models.schemas import StructuredSections, SanitizedContent

logger = logging.getLogger(__name__)

MAX_SECTION_LENGTH = 5000  # Max chars per section

# Control characters and Unicode formatting characters that can be used to hide
# text from a human reviewer while the model still reads it (bidi overrides,
# zero-width joiners, tag characters). Legitimate script — Devanagari names,
# ₹, §, curly quotes — is preserved.
_CONTROL_CHARS = re.compile(
    "["
    "\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f\\x7f"  # C0 controls, keeping \\t \\n \\r
    "\\u0080-\\u009f"                        # C1 controls
    "\\u200b-\\u200f"                        # zero-width space/joiners, LRM/RLM
    "\\u202a-\\u202e"                        # bidi embedding & override
    "\\u2060-\\u2064"                        # word joiner, invisible operators
    "\\u2066-\\u2069"                        # bidi isolates
    "\\ufeff"                                # BOM / zero-width no-break space
    "\\U000e0000-\\U000e007f"                # Unicode tag characters
    "]"
)

# Patterns attempting prompt injection / instruction overrides.
#
# NOTE: this is a *detection* signal, not a security boundary. A blocklist of
# phrasings is trivially bypassed by rewording. The real control is the
# system/user role separation in ai_gate.py and card_generator.py, plus the
# fact that model output is schema-validated and human-reviewed before it can
# be published. Treat hits here as telemetry worth alerting on.
INJECTION_PATTERNS = [
    r'(?i)(ignore|disregard|forget)\s+(all\s+)?(previous|above|prior)\s+(instructions?|prompts?|context)',
    r'(?i)system\s+prompt:',
    r'(?i)you\s+are\s+now\s+a',
    r'(?i)respond\s+only\s+with\s+the\s+following',
    r'(?i)output\s+json\s+saying',
    r'(?i)</?(judgment_sections|section)\b',   # attempts to forge our own delimiters
    r'(?i)\b(card_worthy|is_final_judgment|suggested_category)\b\s*[":=]',  # forging gate output
]


def sanitize_text(text: str, max_length: int = MAX_SECTION_LENGTH) -> str:
    """Sanitize raw text for LLM prompt safety.

    Returns cleaned text. Use scan_for_injection() if you need to know whether
    anything was redacted.
    """
    clean, _ = sanitize_text_with_flags(text, max_length)
    return clean


def sanitize_text_with_flags(text: str, max_length: int = MAX_SECTION_LENGTH) -> tuple[str, list[str]]:
    """Sanitize text and report which injection patterns matched."""
    if not text:
        return "", []

    # 1. Normalize so lookalike/compatibility forms cannot evade the patterns
    clean = unicodedata.normalize("NFKC", text)

    # 2. Truncate to max length
    clean = clean[:max_length]

    # 3. Strip control and invisible formatting characters.
    #    The previous filter was [^\x20-\x7E\n\r\t], which deleted ALL non-ASCII
    #    text — "₹50,000 fine" silently became "50,000 fine", and party names
    #    with diacritics were mangled before reaching the model.
    clean = _CONTROL_CHARS.sub("", clean)

    # 4. Redact prompt injection patterns
    matched: list[str] = []
    for pattern in INJECTION_PATTERNS:
        clean, count = re.subn(pattern, '[REDACTED_INJECTION_ATTEMPT]', clean)
        if count:
            matched.append(pattern)

    return clean.strip(), matched


def sanitize_prompt_input(text: str, max_length: int = MAX_SECTION_LENGTH) -> str:
    """Sanitize a single free-text blob destined for an LLM prompt.

    Used by the rights pipeline, which previously passed raw scraped statute
    text straight into the prompt with no sanitization at all.
    """
    clean, matched = sanitize_text_with_flags(text, max_length)
    if matched:
        logger.warning(
            "Redacted %d injection pattern(s) from prompt input", len(matched)
        )
    return clean


def sanitize_structured_sections(sections: StructuredSections) -> SanitizedContent:
    """Sanitize structured sections, compute SHA-256 hash, and format into XML delimiters."""
    raw_dict = sections.to_dict()

    sanitized_dict: dict[str, str] = {}
    xml_lines: list[str] = ["<judgment_sections>"]
    all_matches: list[str] = []

    # Allowed keys only
    allowed_keys = {"Facts", "Issue", "Conclusion", "Arguments", "Order"}

    for key, text in raw_dict.items():
        if key not in allowed_keys or not text:
            continue

        clean_val, matched = sanitize_text_with_flags(text)
        all_matches.extend(matched)
        if clean_val:
            sanitized_dict[key] = clean_val
            # S-1: Escape XML special chars in content so document text cannot
            # break the XML structure or inject rogue tags into the LLM prompt
            escaped_val = html.escape(clean_val)
            xml_lines.append(f'  <section name="{key}">\n{escaped_val}\n  </section>')

    xml_lines.append("</judgment_sections>")
    xml_prompt_block = "\n".join(xml_lines)

    if all_matches:
        logger.warning(
            "Redacted %d injection pattern match(es) while sanitizing judgment sections",
            len(all_matches),
        )

    # Hash the canonical JSON of the sanitized dict for tamper detection.
    # repr(sorted(...)) was ambiguous — quoting and escaping differences across
    # Python versions change the digest for identical content.
    canonical_text = json.dumps(sanitized_dict, sort_keys=True, ensure_ascii=False).encode("utf-8")
    content_hash = hashlib.sha256(canonical_text).hexdigest()

    return SanitizedContent(
        sections=sanitized_dict,
        content_hash=content_hash,
        xml_prompt_block=xml_prompt_block
    )
