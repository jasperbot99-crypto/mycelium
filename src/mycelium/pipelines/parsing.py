"""Content parsing — server-side extraction logic for raw client inputs.

Pure functions, no I/O, no side effects. Ported from mycelium-connector
TypeScript plugin to centralize parsing on the server.

Functions:
- looks_like_garbage: reject JSON fragments, escaped strings, credentials
- extract_subject: 6-strategy cascade for subject extraction
- parse_memory_store_to_fact: parse [agent] [type] content format
- extract_entities: known names, capitalized phrases, quoted strings
- extract_query_from_context: combine task name with extracted entities
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from mycelium.domain.types import FactContent, SourceType


@dataclass(frozen=True)
class ParsedFact:
    """Result of parsing a raw memory_store input into structured fact data."""

    content: FactContent
    source_type: SourceType
    tags: list[str]


# ─── Known Entities ──────────────────────────────────────────────────────────

KNOWN_ENTITIES: list[str] = [
    "Mycelium", "OpenClaw", "LanceDB", "Supabase", "Alpaca",
    "Telegram", "GitHub", "launchd", "BTCUSDT", "EUR/USD",
    "jasper-code", "jasper-trader", "jasper-research", "jasper-planner",
    "spawn-enforcer", "trading-enforcer", "shared-cognition", "procedural-memory",
    "ControlPlane", "Next.js", "pgvector", "rumination",
]

# ─── Compiled Patterns ───────────────────────────────────────────────────────

_TICKER_RE = re.compile(r"\b([A-Z]{2,6}/[A-Z]{2,6}|[A-Z]{3,}USDT?|BTC|ETH)\b")
_BACKTICK_RE = re.compile(r"`([^`]{2,40})`")
_QUOTED_RE = re.compile(r'"([^"]{2,40})"')
_LEADING_PROPER_RE = re.compile(
    r"^([A-Z][a-zA-Z0-9.*/-]+(?:\s+[a-zA-Z0-9.*/-]+){0,3})"
)
_TRAILING_VERB_RE = re.compile(
    r"\s+(?:is|are|was|were|has|have|had|requires?|should|must|can|will|does|did"
    r"|may|might|shall|need|seems?)$",
    re.IGNORECASE,
)
_FIRST_NOUN_RE = re.compile(
    r"^([a-zA-Z][a-zA-Z0-9\s./-]{2,40}?)"
    r"(?:\s+(?:is|are|was|were|has|have|had|requires?|should|must|can|will|does|did"
    r"|confirmed|outperforms?|unreliable|fails?)\b)",
    re.IGNORECASE,
)
_CAPS_PHRASE_RE = re.compile(r"\b([A-Z][a-zA-Z0-9]+(?:[\s\-][A-Z0-9][a-zA-Z0-9]*)*)\b")
_IDENTIFIER_RE = re.compile(r"[a-z][a-z0-9]*[-_][a-z0-9]+(?:[-_][a-z0-9]+)*")
_COMMON_WORDS = frozenset({
    "The", "This", "That", "These", "Those", "What", "Where", "When", "How",
    "Why", "Can", "Could", "Would", "Should", "Will", "Do", "Does", "Did",
    "Is", "Are", "Was", "Were", "Has", "Have", "Had", "If", "But", "And",
    "For", "Not", "You", "Your", "It", "Its", "My", "Our", "We", "They",
    "He", "She", "Her", "Him", "Let", "Just", "Also", "Here", "There",
    "After", "Before", "From", "With",
})

_MEMORY_STORE_RE = re.compile(r"^\[([^\]]+)\]\s*\[([^\]]+)\]\s*(.*)", re.DOTALL)
_JSON_BOOKEND_START_RE = re.compile(r"^\s*[\[{]")
_JSON_BOOKEND_END_RE = re.compile(r"[\]}]\s*$")
_ESCAPED_QUOTE_RE = re.compile(r'\\"')
_JSON_CHARS_RE = re.compile(r"[{}\[\]:,]")
_SECRET_PATTERN_RE = re.compile(r"password|secret|token|api.?key", re.IGNORECASE)
_SECRET_VALUE_RE = re.compile(r"[=:]\s*\S{10,}")


# ─── Public Functions ────────────────────────────────────────────────────────


def looks_like_garbage(text: str) -> bool:
    """Detect garbage inputs that should never be ingested.

    Rejects JSON fragments, escaped string leakage, high-density JSON chars,
    and credential/secret patterns.
    """
    # Raw JSON objects/arrays
    if _JSON_BOOKEND_START_RE.search(text) and _JSON_BOOKEND_END_RE.search(text):
        return True
    # Contains escaped quotes (JSON string leakage)
    if len(_ESCAPED_QUOTE_RE.findall(text)) > 2:
        return True
    # High density of JSON structure chars
    json_chars = len(_JSON_CHARS_RE.findall(text))
    if json_chars > len(text) * 0.15:
        return True
    # Password/secret patterns
    return bool(_SECRET_PATTERN_RE.search(text) and _SECRET_VALUE_RE.search(text))


def extract_subject(content: str) -> str:
    """Extract a meaningful subject from memory content.

    6-strategy cascade:
    1. Ticker patterns (EUR/USD, BTCUSDT, BTC, ETH)
    2. Backtick-quoted code identifiers
    3. Double-quoted strings
    4. Leading proper noun phrase
    5. First noun before a verb/preposition
    6. Fallback: first 50 chars at word boundary
    """
    # 1. Ticker patterns
    ticker = _TICKER_RE.search(content)
    if ticker:
        after = content[ticker.end():]
        next_term = re.match(r"^\s+([A-Z][A-Za-z0-9]+)", after)
        return f"{ticker.group(1)} {next_term.group(1)}" if next_term else ticker.group(1)

    # 2. Backtick-quoted code identifiers
    backtick = _BACKTICK_RE.search(content)
    if backtick:
        return backtick.group(1)

    # 3. Double-quoted strings
    quoted = _QUOTED_RE.search(content)
    if quoted:
        return quoted.group(1)

    # 4. Leading proper noun phrase
    leading = _LEADING_PROPER_RE.match(content)
    if leading:
        trimmed = _TRAILING_VERB_RE.sub("", leading.group(1)).strip()
        if len(trimmed) > 2:
            return trimmed

    # 5. First noun before a verb
    first_noun = _FIRST_NOUN_RE.match(content)
    if first_noun:
        return first_noun.group(1).strip()

    # 6. Fallback: first 50 chars at word boundary
    fallback = content[:50]
    last_space = fallback.rfind(" ")
    return fallback[:last_space].strip() if last_space > 10 else fallback.strip()


def parse_memory_store_to_fact(
    input_text: str,
    agent_id: str,
) -> ParsedFact | None:
    """Parse a memory_store input string into a structured ParsedFact.

    Expected format: ``[agent-id] [type] content``

    Returns None if the input is garbage, too short, or unparseable.
    """
    if not input_text or len(input_text) < 10:
        return None
    if looks_like_garbage(input_text):
        return None

    # Canonical format: [agent-id] [type] content
    match = _MEMORY_STORE_RE.match(input_text)
    if match:
        source_agent = match.group(1)
        fact_type = match.group(2)
        content_text = match.group(3).strip()
        if len(content_text) < 5:
            return None

        predicate = fact_type.lower().replace(" ", "_")
        context = f"via {source_agent}" if source_agent != agent_id else None

        return ParsedFact(
            content=FactContent(
                subject=extract_subject(content_text),
                predicate=predicate,
                object=content_text[:500],
                context=context,
            ),
            source_type=SourceType.AGENT_EXTRACTION,
            tags=[agent_id, fact_type.lower(), source_agent],
        )

    # Non-bracketed: only ingest if it looks like a real sentence
    if "{" in input_text or "[" in input_text or len(input_text.split(" ")) < 3:
        return None

    return ParsedFact(
        content=FactContent(
            subject=extract_subject(input_text),
            predicate="has_memory",
            object=input_text[:500],
        ),
        source_type=SourceType.AGENT_INFERENCE,
        tags=[agent_id],
    )


def extract_entities(
    text: str,
    known_entities: list[str] | None = None,
) -> list[str]:
    """Pull meaningful entities from text.

    Extracts proper nouns, known project/tool names, capitalized multi-word
    phrases, quoted strings, and hyphenated/underscored identifiers.
    Returns up to 8 entities.
    """
    entities: list[str] = []
    seen: set[str] = set()

    def _add(s: str) -> None:
        key = s.lower()
        if key not in seen and len(key) > 2:
            seen.add(key)
            entities.append(s)

    # Known entity scan
    for entity in (known_entities or KNOWN_ENTITIES):
        if entity.lower() in text.lower():
            _add(entity)

    # Capitalized phrases
    for m in _CAPS_PHRASE_RE.finditer(text):
        phrase = m.group(1)
        if phrase not in _COMMON_WORDS:
            _add(phrase)

    # Quoted strings (max 3)
    for m in list(_QUOTED_RE.finditer(text))[:3]:
        _add(m.group(1))

    # File paths / identifiers
    for m in list(_IDENTIFIER_RE.finditer(text))[:5]:
        if len(m.group(0)) > 5:
            _add(m.group(0))

    return entities[:8]


def extract_query_from_context(
    task_name: str | None = None,
    prompt: str | None = None,
) -> str | None:
    """Build a query string from task context and prompt entities.

    Returns None if no meaningful entities can be extracted.
    """
    parts: list[str] = []

    if task_name:
        parts.append(task_name)

    if prompt:
        entities = extract_entities(prompt)
        if entities:
            parts.append(" ".join(entities))

    return " ".join(parts) if parts else None
