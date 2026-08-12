# LegalX Shorts — Backend

> AI-powered legal awareness flashcard pipeline for the Indian legal system.

LegalX Shorts transforms raw Indian court judgments and statutory Bare Acts into short, plain-language Q&A flashcard content — ready for human review before being published to the LegalX mobile app.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Project Structure](#project-structure)
4. [Data Models & Schemas](#data-models--schemas)
5. [Ingestion Pipelines](#ingestion-pipelines)
   - [Judgments Pipeline](#1-judgments-pipeline)
   - [Statutory Rights Pipeline](#2-statutory-rights-pipeline)
6. [API Reference](#api-reference)
   - [GET /feed](#get-feed)
   - [GET /preview](#get-preview)
   - [Preview Actions](#preview-actions)
   - [GET /health](#get-health)
7. [Staging & Review System](#staging--review-system)
8. [Database — Supabase](#database--supabase)
9. [Security Architecture](#security-architecture)
10. [Configuration](#configuration)
11. [Local Development Setup](#local-development-setup)
12. [Running the Pipelines](#running-the-pipelines)
13. [Running the API Server](#running-the-api-server)
14. [Testing](#testing)
15. [Roadmap](#roadmap)

---

## Overview

LegalX Shorts is a backend content ingestion and serving system. It has two primary jobs:

1. **Ingest** — Automatically fetch court judgments and statutory sections from [IndianKanoon](https://indiankanoon.org), filter them through an AI quality gate, and generate human-readable Q&A flashcards.
2. **Serve** — Expose a paginated REST API (`GET /feed`) that delivers approved flashcards to the LegalX mobile app.

A human reviewer approves or rejects every AI-generated card before it is published. New cards are never visible to end users until a reviewer explicitly approves them.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      INGESTION PIPELINE                          │
│                                                                  │
│  IndianKanoon API                                                │
│       │                                                          │
│       ▼                                                          │
│  IndianKanoonClient  ──search──▶  filter_search_results          │
│       │                               │                          │
│       │ (dedup against seen TIDs)     │                          │
│       ▼                               ▼                          │
│  fetch_document()            StructuralParser                    │
│       │                         (BeautifulSoup)                  │
│       │                               │                          │
│       ▼                               ▼                          │
│  SanitizedContent ◀── sanitize_structured_sections()            │
│       │                                                          │
│       ▼                                                          │
│   AI Gate (Llama-3.1-8B via Groq)                               │
│   "Is this card-worthy? What category?"                         │
│       │                                                          │
│   card_worthy=true?                                              │
│       │                                                          │
│       ▼                                                          │
│  Card Generator (Llama-3.3-70B via Groq)                        │
│  "Generate Q&A flashcard"                                        │
│       │                                                          │
│       ▼                                                          │
│  ContentRepository.save_card()                                   │
│       │                 │                                        │
│   Supabase DB      staging/candidates.jsonl                      │
│  (is_published=false)  (JSONL fallback)                          │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                      REVIEWER WORKFLOW                           │
│                                                                  │
│  GET /preview  ──▶  Staged cards shown in browser visualizer    │
│                                                                  │
│  POST /preview/approve/{id}  ──▶  is_published = True           │
│  POST /preview/reject/{id}   ──▶  Card removed from staging     │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                      PUBLIC API                                  │
│                                                                  │
│  GET /feed?category=cyber&content_type=judgment_summary         │
│       │                                                          │
│  ContentRepository.get_feed()                                    │
│       │                    │                                     │
│  Supabase (is_published=true)   JSONL fallback                  │
│       │                                                          │
│  FeedResponse ──▶  LegalX Mobile App                            │
└─────────────────────────────────────────────────────────────────┘
```

---

## Project Structure

```
legalx-shorts-backend/
│
├── app/
│   ├── main.py                      # FastAPI app factory, middleware, router registration
│   ├── config.py                    # Pydantic Settings — loads .env.development or .env
│   │
│   ├── models/
│   │   └── schemas.py               # All Pydantic models and enums (Category, ContentType, etc.)
│   │
│   ├── ingestion/
│   │   ├── indiankanoon_client.py   # IndianKanoon API client (search + fetch document)
│   │   ├── filters.py               # Pre-filter: dedup, non-judgment blocklist, docsize check
│   │   ├── structural_parser.py     # Parse HTML data-structure tags into StructuredSections
│   │   ├── sanitizer.py             # Truncate, redact prompt injection, compute SHA-256
│   │   ├── ai_gate.py               # AI triage: card-worthy? correct category? (Llama-3.1-8B)
│   │   ├── card_generator.py        # Q&A flashcard generator (Llama-3.3-70B)
│   │   ├── pipeline.py              # Orchestrator: judgments end-to-end pipeline
│   │   ├── rights_summarizer.py     # Statutory summary generator (Llama-3.3-70B)
│   │   └── rights_pipeline.py       # Orchestrator: statutory Bare Acts end-to-end pipeline
│   │
│   ├── repository/
│   │   └── content_repository.py   # Supabase (live) + JSONL (fallback) persistence layer
│   │
│   ├── api/
│   │   ├── feed.py                  # GET /feed — public flashcard feed endpoint
│   │   └── preview.py               # GET /preview — interactive reviewer web visualizer
│   │
│   └── lib/
│       └── supabase_client.py       # Lazy Supabase client initialization with placeholder detection
│
├── scripts/
│   ├── run_pipeline.py              # CLI runner for the Judgments Ingestion Pipeline
│   └── run_rights_pipeline.py       # CLI runner for the Statutory Rights Pipeline
│
├── tests/
│   ├── test_client.py               # IndianKanoon client: search, fetch, budget guard
│   ├── test_filters.py              # Filter logic: blocklist, dedup, docsize
│   ├── test_schemas.py              # Pydantic model validation
│   ├── test_phase3.py               # Parser, sanitizer, AI gate, card generator (mocked)
│   ├── test_phase4.py               # Full pipeline orchestrator (mocked)
│   ├── test_phase6.py               # Supabase client and repository fallback
│   ├── test_feed_api.py             # Feed API endpoint integration tests
│   └── test_rights_pipeline.py     # Rights pipeline: extractor, summarizer, orchestrator
│
├── staging/
│   ├── candidates.jsonl             # Staged cards awaiting human review (not yet published)
│   ├── ai_gate_log.jsonl            # Audit log of every AI Gate decision (pass and reject)
│   └── run_stats.jsonl              # Per-run ingestion statistics log
│
├── shorts_schema.sql                # Supabase PostgreSQL DDL — run once in SQL Editor
├── requirements.txt                 # Python dependencies
├── .env.example                     # Environment variable template
└── .env.development                 # Local dev secrets (git-ignored)
```

---

## Data Models & Schemas

All models are defined in [`app/models/schemas.py`](app/models/schemas.py).

### Enums

| Enum | Values |
|------|--------|
| `Category` | `cyber`, `traffic`, `posco`, `consumer`, `cheque_ni_act` |
| `ContentType` | `judgment_summary`, `rights_explainer` |

### Core Models

| Model | Purpose |
|-------|---------|
| `SearchResult` | Single result from IndianKanoon search (tid, title, docsource, docsize) |
| `FetchedDocument` | Full document from IndianKanoon (tid, title, raw HTML, docsource) |
| `StructuredSections` | Parsed judgment sections (facts, issue, conclusion, arguments, order) |
| `SanitizedContent` | LLM-safe content: sanitized dict, SHA-256 hash, XML prompt block |
| `GateVerdict` | AI Gate output: card_worthy, reasoning, is_final_judgment, suggested_category |
| `CardDraft` | Generated Q&A flashcard: question, direct_answer, explanation, case_reference, suggested_questions |
| `FeedCard` | API response card shape served to the mobile app |
| `FeedResponse` | Paginated API response: list of FeedCards + next_cursor |

---

## Ingestion Pipelines

### 1. Judgments Pipeline

**File:** [`app/ingestion/pipeline.py`](app/ingestion/pipeline.py)  
**Runner:** [`scripts/run_pipeline.py`](scripts/run_pipeline.py)

Fetches the latest Supreme Court judgments from IndianKanoon, evaluates each through the AI Gate, and generates Q&A flashcards for human review.

#### Pipeline Steps

```
Step 1 — Search IndianKanoon
  Query: "<category topic> doctypes: supremecourt fromdate: 1-1-2024"
  e.g.  "Negotiable Instruments Act ORR Section 138 doctypes: supremecourt fromdate: 1-1-2024"

Step 2 — Pre-filter
  • Deduplicate against seen TIDs from candidates.jsonl + ai_gate_log.jsonl
  • Block non-judgment sources (Lok Sabha, Law Commission, Press Bureau, etc.)
  • Soft-reject documents below 100 bytes (docsize check)

Step 3 — Fetch Document
  • POST /doc/{tid}/ — retrieve full HTML judgment text
  • Respectful 1-second delay between calls

Step 4 — Structural Parsing (BeautifulSoup)
  • Extract sections from HTML data-structure attributes:
    data-structure="Facts" → facts
    data-structure="Issue" → issue
    data-structure="Conclusion" → conclusion
    data-structure="Arguments" → arguments
    data-structure="Order" → order
  • Fallback heuristic: split paragraphs into Facts (first half) + Conclusion (second half)
  • Skip if has_substance() is False (no facts + conclusion = thin procedural order)

Step 5 — Sanitization
  • Truncate each section to 5,000 characters
  • Strip non-printable/control characters
  • Redact prompt injection patterns (e.g. "ignore previous instructions")
  • Compute SHA-256 hash of sanitized content for tamper detection
  • Wrap in XML delimiters for safe, structured LLM prompting

Step 6 — AI Gate (Llama-3.1-8B-instant via Groq)
  • Classifies judgment as card_worthy: true | false
  • Assigns category: cyber | traffic | posco | consumer | cheque_ni_act | other
  • Logs every decision (pass and reject) to staging/ai_gate_log.jsonl
  • Skips if category does not match the pipeline's target category

Step 7 — Card Generator (Llama-3.3-70B-versatile via Groq)
  • Generates: question, direct_answer, explanation, suggested_questions
  • case_reference is always hardcoded from IndianKanoon API metadata — never LLM-generated
    (prevents citation hallucination)

Step 8 — Save via ContentRepository
  • If Supabase is configured: inserts into shorts_cards (is_published=false)
  • If Supabase is placeholder: appends to staging/candidates.jsonl

Step 9 — Write run_stats.jsonl
  • Records timestamp, category, query, found_count, docs_fetched, cards_published
```

#### Category → Query Mapping

| Category | IndianKanoon Query |
|----------|-------------------|
| `cyber` | `Information Technology Act ORR cyber fraud` |
| `traffic` | `Motor Vehicles Act ORR road accident` |
| `posco` | `Protection of Children from Sexual Offences Act ORR POCSO` |
| `consumer` | `Consumer Protection Act` |
| `cheque_ni_act` | `Negotiable Instruments Act ORR Section 138` |

---

### 2. Statutory Rights Pipeline

**File:** [`app/ingestion/rights_pipeline.py`](app/ingestion/rights_pipeline.py)  
**Runner:** [`scripts/run_rights_pipeline.py`](scripts/run_rights_pipeline.py)

Fetches statutory Bare Act sections from IndianKanoon (`doctypes: acts`) and summarizes them into plain-language citizen-rights cards. Designed for the **"Know Your Rights"** tab in the mobile app.

#### Pipeline Steps

```
Step 1 — Search IndianKanoon (doctypes: acts)
  Query: "<Act Name> doctypes: acts"
  e.g.  "Protection of Children from Sexual Offences Act doctypes: acts"

Step 2 — Filter to statutory sources
  Include results where docsource contains "section", "union of india"

Step 3 — Fetch + Extract Text
  • POST /doc/{tid}/ — retrieve raw HTML
  • BeautifulSoup strips <script>, <style>, <a>, <form> tags
  • Returns clean section text

Step 4 — Summarize (Llama-3.3-70B)
  Two modes:
  A. single_card=True  (default): Consolidate multiple sections into ONE master card
     → Best for Acts you want shown as a single comprehensive card
  B. single_card=False: Generate one card per section

  Sensitivity rules enforced in RIGHTS_SUMMARIZER_SYSTEM_PROMPT:
  • No graphic or clinical terminology in headlines
  • Headings framed around protection, safety, awareness, and legal remedies
  • Output: title, summary (2-3 plain sentences), statute_reference

Step 5 — Save via ContentRepository (is_published=false)
  content_type = "rights_explainer"
```

---

## API Reference

### GET /feed

Returns a paginated list of approved (`is_published=true`) flashcards.

**URL:** `GET /feed`

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `category` | enum | `null` (all) | Filter by category: `cyber`, `traffic`, `posco`, `consumer`, `cheque_ni_act` |
| `content_type` | enum | `judgment_summary` | Card type: `judgment_summary` or `rights_explainer` |
| `cursor` | string (UUID) | `null` | Pagination cursor (ID of last card from previous page) |
| `limit` | int | `20` | Cards per page (min 1, max 50) |

**Response:**

```json
{
  "cards": [
    {
      "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "content_type": "judgment_summary",
      "category": "cheque_ni_act",
      "title": "Does a company director automatically get cleared in a cheque bounce case?",
      "card_text": "Q: Does a company director...\n\nA: No...\n\nExplanation...",
      "source_url": "https://indiankanoon.org/doc/12345678/",
      "published_at": "2025-07-24T08:00:00+00:00"
    }
  ],
  "next_cursor": "3fa85f64-5717-4562-b3fc-2c963f66afa6"
}
```

To fetch the next page, pass `next_cursor` value as the `cursor` parameter. When `next_cursor` is `null`, you have reached the last page.

---

### GET /preview

**URL:** `GET /preview`

Opens the interactive **Founder & Reviewer Visualizer** — a minimalist browser-based mobile mockup for reviewing staged candidate cards before they go live.

**Features:**
- **🏛️ Judgments Reel tab** — shows all staged `judgment_summary` cards
- **📜 Know Your Rights tab** — shows all staged `rights_explainer` cards
- **Approve & Publish** button — sets `is_published = true`
- **Discard** button — removes the card from staging
- Forward/back navigation with card counter

> Access at: **http://localhost:8000/preview**

---

### Preview Actions

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/preview/data` | GET | Returns raw JSON array of all staged cards |
| `/preview/approve/{card_id}` | POST | Sets `is_published = true` for the card in staging |
| `/preview/reject/{card_id}` | POST | Removes card from staging file |

These endpoints operate on `staging/candidates.jsonl` in local dev mode. In production, they will write to Supabase.

---

### GET /health

**URL:** `GET /health`

```json
{ "status": "ok", "environment": "development" }
```

---

## Staging & Review System

In local development (no live Supabase credentials), the system uses three JSONL files in the `staging/` directory:

### `staging/candidates.jsonl`

Every card generated by the pipeline is appended here with `is_published: false`. This is the reviewer's queue. Cards in this file are not visible to app users.

Each line is a complete JSON card record:

```json
{
  "id": "uuid",
  "content_type": "judgment_summary",
  "category": "cheque_ni_act",
  "title": "Question text...",
  "question": "Question text...",
  "direct_answer": "Immediate answer...",
  "explanation": "Plain-language explanation...",
  "card_text": "Q: ...\n\nA: ...\n\nExplanation...",
  "case_reference": "Case Name (Supreme Court of India)",
  "suggested_questions": ["Follow-up 1", "Follow-up 2"],
  "source_url": "https://indiankanoon.org/doc/12345678/",
  "source_tid": "12345678",
  "content_hash": "sha256hex...",
  "is_published": false,
  "published_at": "2025-07-24T08:00:00+00:00",
  "created_at": "2025-07-24T08:00:00+00:00"
}
```

### `staging/ai_gate_log.jsonl`

**The AI Gate Audit Trail.** Every document evaluated by the AI Gate is logged here — both those that passed (`card_worthy: true`) and those that were rejected (`card_worthy: false`).

```json
{
  "content_hash": "sha256hex...",
  "card_worthy": false,
  "reasoning": "This is a procedural/interim order with no substantive legal holding.",
  "is_final_judgment": false,
  "suggested_category": "other"
}
```

The pipeline reads `source_tid` values from this file before each run, so **documents rejected by the AI Gate are never re-fetched or re-processed** — saving API costs.

### `staging/run_stats.jsonl`

Appended at the end of every pipeline run for operational monitoring.

```json
{
  "timestamp": "2025-07-24T08:00:00+00:00",
  "category": "cyber",
  "query_used": "Information Technology Act ORR cyber fraud doctypes: supremecourt fromdate: 1-1-2024",
  "found_count": 10,
  "docs_fetched": 3,
  "cards_published": 2
}
```

---

## Database — Supabase

When real Supabase credentials are configured, the system switches from JSONL files to a live PostgreSQL database automatically.

### Setup

1. Create a Supabase project at [supabase.com](https://supabase.com)
2. Open the Supabase **SQL Editor**
3. Paste and run the entire contents of [`shorts_schema.sql`](shorts_schema.sql)

### Schema: `shorts_cards` Table

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID (PK) | Auto-generated primary key |
| `content_type` | ENUM | `judgment_summary` or `rights_explainer` |
| `category` | ENUM | `cyber`, `traffic`, `posco`, `consumer`, `cheque_ni_act` |
| `title` | TEXT | Card headline / scenario question |
| `question` | TEXT | Q&A question text |
| `direct_answer` | TEXT | Immediate one-sentence answer |
| `explanation` | TEXT | Plain-language explanation (~150 words) |
| `card_text` | TEXT | Pre-formatted card text for the mobile feed |
| `case_reference` | TEXT | Hardcoded case citation from IndianKanoon metadata |
| `suggested_questions` | JSONB | Array of 2-3 follow-up questions |
| `source_url` | TEXT | Link to the original IndianKanoon document |
| `source_tid` | TEXT (UNIQUE) | IndianKanoon document TID — used for deduplication |
| `content_hash` | TEXT | SHA-256 of sanitized content for tamper detection |
| `is_published` | BOOLEAN | `false` by default. Set to `true` by reviewer to publish. |
| `published_at` | TIMESTAMPTZ | Timestamp of publishing |
| `created_at` | TIMESTAMPTZ | Timestamp of card creation |

### Indexes

Three partial indexes are created for fast feed queries:

```sql
-- Fast category-filtered feed queries (only published cards)
CREATE INDEX idx_shorts_cards_category     ON shorts_cards(category, published_at DESC) WHERE is_published = true;

-- Fast content_type-filtered feed queries
CREATE INDEX idx_shorts_cards_content_type ON shorts_cards(content_type, published_at DESC) WHERE is_published = true;

-- Fast deduplication check by TID
CREATE INDEX idx_shorts_cards_source_tid   ON shorts_cards(source_tid) WHERE source_tid IS NOT NULL;
```

### Row Level Security (RLS)

Two RLS policies enforce a **zero data breach architecture**:

| Policy | Action | Condition |
|--------|--------|-----------|
| `Public published read policy` | SELECT | `is_published = true` only |
| `Reviewer approve policy` | UPDATE | JWT `app_metadata.role` is `reviewer` or `admin` |

App users can never read unpublished staging cards. Only authenticated reviewers can approve/reject.

---

## Security Architecture

| Threat | Mitigation |
|--------|-----------|
| **Unreviewed content reaching users** | `is_published = true` is filtered on **every** read path — Supabase *and* the JSONL fallback. Both pipelines write `is_published: false` with `published_at: null`; only a reviewer's approval flips them. A DB `CHECK` constraint enforces that pairing. |
| **Unauthorized publishing / deletion** | The `/preview/*` endpoints require HTTP Basic reviewer credentials. They are not mounted at all unless `ENABLE_PREVIEW_UI` is on, and in production they refuse to mount without a username and a ≥12-character password. Approvals and rejections are logged with the reviewer name. |
| **Stored XSS in the reviewer tool** | The preview page builds all card markup with `createElement` / `textContent`. `source_url` is scheme-checked (`http`/`https` only) before becoming an `href`. A CSP is sent on every response. |
| **API Abuse / DDoS** | `slowapi` rate limits applied **as decorators** on `/feed` (`FEED_RATE_LIMIT`, default 30/min) and every `/preview` route. `X-Forwarded-For` is honoured **only** when the peer is listed in `TRUSTED_PROXIES` — see [Running the API Server](#running-the-api-server), because uvicorn's own defaults can override this. |
| **Prompt Injection** | Both LLM paths sanitize input (the rights pipeline included), normalize Unicode, strip invisible/bidi characters, XML-escape content, and put instructions in the `system` role with untrusted text in the `user` role. The regex blocklist is a **detection signal, not a boundary** — the real controls are role separation, schema validation, and human review. |
| **API Reconnaissance** | `/docs` and `/openapi.json` are disabled in `production`. The `Server` header is suppressed. |
| **Hung Workers** | All IndianKanoon calls have a 30s timeout, bounded retries with backoff on 429/5xx, and an 8 MB response cap. |
| **Database Exposure** | Public reads use `SUPABASE_ANON_KEY` so RLS actually applies; `SUPABASE_SERVICE_KEY` (which **bypasses RLS**) is reserved for ingestion writes. Set both. |
| **CORS** | Configured via `ALLOWED_ORIGINS`. `*` is rejected at startup because credentials are enabled. |
| **Secret Leakage** | Supabase client returns `None` on `"placeholder"` credentials. `.env.development` is never used as a production fallback — production reads `.env` only. |
| **Data loss / Race Conditions** | JSONL appends hold an `flock` for the whole write; read-modify-write cycles replace the file atomically via `os.replace`. |
| **Silent write failure** | A failed Supabase insert goes to `staging/failed_saves.jsonl` and raises. It is never downgraded to a `candidates.jsonl` write, which previously pushed unreviewed cards into the file the feed reads. |
| **HTTPS** | `HTTPSRedirectMiddleware` plus HSTS in `production`. |
| **Budget Overrun** | The IndianKanoon call cap is enforced in **every** environment (`MAX_IKANOON_CALLS`, `DEV_MAX_IKANOON_CALLS`). Dedup is checked against the real backend before each paid fetch, and the AI gate log records `source_tid` so rejected documents are not re-fetched. |

---

## Configuration

Copy `.env.example` to `.env.development` and fill in your credentials:

```bash
cp .env.example .env.development
```

| Variable | Required | Description |
|----------|----------|-------------|
| `ENVIRONMENT` | Yes | `development` or `production` |
| `IKANOON_TOKEN` | Yes | IndianKanoon API token (get at indiankanoon.org/api) |
| `GROQ_API_KEY` | Yes | Groq API key (get at console.groq.com) |
| `SUPABASE_URL` | Optional | Your Supabase project URL (`https://xxx.supabase.co`) |
| `SUPABASE_SERVICE_KEY` | Optional | Service role key — **bypasses RLS**. Ingestion writes only. |
| `SUPABASE_ANON_KEY` | Recommended | Anon key used for the public feed read path so RLS applies. Without it the feed falls back to the service key and logs a warning. |
| `GROQ_GATE_MODEL` | Optional | AI Gate model (default: `llama-3.1-8b-instant`) |
| `GROQ_GENERATOR_MODEL` | Optional | Card generator model (default: `llama-3.3-70b-versatile`) |
| `MAX_IKANOON_CALLS` | Optional | Production budget cap per run (default: `500`) |
| `DEV_MAX_IKANOON_CALLS` | Optional | Dev budget cap for IndianKanoon API calls (default: `50`) |
| `ALLOWED_ORIGINS` | Optional | Comma-separated or JSON CORS origins. `*` is rejected. |
| `FEED_RATE_LIMIT` | Optional | slowapi syntax (default: `30/minute`) |
| `PREVIEW_RATE_LIMIT` | Optional | slowapi syntax (default: `60/minute`) |
| `TRUSTED_PROXIES` | **Yes, if behind a proxy** | Comma-separated IPs/CIDRs allowed to set `X-Forwarded-For`. Empty means the header is ignored entirely. |
| `ENABLE_PREVIEW_UI` | Optional | Mount the reviewer tool. Defaults to on in development, off in production. |
| `REVIEWER_USERNAME` | **Yes in production** | HTTP Basic username for `/preview/*`. |
| `REVIEWER_PASSWORD` | **Yes in production** | HTTP Basic password, minimum 12 characters. |

> **Note:** If `SUPABASE_URL` or `SUPABASE_SERVICE_KEY` contain the word `"placeholder"`, the system automatically falls back to JSONL file storage. No code changes needed to switch between modes.

---

## Local Development Setup

### Prerequisites

- Python 3.11+
- An [IndianKanoon API token](https://indiankanoon.org/api/)
- A [Groq API key](https://console.groq.com)

### Install

```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set up environment variables
cp .env.example .env.development
# Edit .env.development with your real API keys
```

---

## Running the Pipelines

Both pipeline runners are standalone CLI scripts. Run them from the project root.

### Judgments Ingestion Pipeline

```bash
# Default: 3 cyber fraud Supreme Court judgments from 2024 onwards
python scripts/run_pipeline.py

# Custom run
python scripts/run_pipeline.py --category cheque_ni_act --max-docs 5 --from-date 1-1-2025

# All available categories
python scripts/run_pipeline.py --category cyber
python scripts/run_pipeline.py --category traffic
python scripts/run_pipeline.py --category posco
python scripts/run_pipeline.py --category consumer
python scripts/run_pipeline.py --category cheque_ni_act
```

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--category` | `cyber` | Category to ingest |
| `--max-docs` | `3` | Maximum documents to fetch and process |
| `--from-date` | `1-1-2024` | Earliest judgment date filter (`D-M-YYYY`) |

---

### Statutory Rights Pipeline

```bash
# Default: POCSO Act (3 sections, single consolidated card)
python scripts/run_rights_pipeline.py

# Custom Act
python scripts/run_rights_pipeline.py \
  --act "Consumer Protection Act" \
  --category consumer \
  --max-sections 5
```

**Options:**

| Flag | Default | Description |
|------|---------|-------------|
| `--act` | `Protection of Children from Sexual Offences Act` | Full name of the Bare Act |
| `--category` | `posco` | Category enum string |
| `--max-sections` | `3` | Maximum number of act sections to fetch |

After running either pipeline, new cards appear in `staging/candidates.jsonl` with `is_published: false`. Open the [preview visualizer](#get-preview) to review them.

---

## Running the API Server

Use the provided entrypoint rather than a bare `uvicorn` command:

```bash
# Development (auto-reload on file changes)
python scripts/serve.py --reload --port 8000

# Production
python scripts/serve.py --host 0.0.0.0 --port 8000
```

### Why not plain `uvicorn app.main:app`?

Uvicorn enables `ProxyHeadersMiddleware` **by default** with
`forwarded_allow_ips="127.0.0.1"`. That middleware rewrites `request.client.host`
from `X-Forwarded-For` *before* any application code runs, so a client can forge
the header and get a fresh rate-limit bucket per request — regardless of what the
app checks. This is measurable: with uvicorn's defaults, requests carrying
different forged `X-Forwarded-For` values all returned `200` after the limit was
exhausted; through `scripts/serve.py` they correctly return `429`.

`scripts/serve.py` binds uvicorn's proxy trust to the same `TRUSTED_PROXIES`
setting the application uses, so there is one place to configure it:

- `TRUSTED_PROXIES` empty → forwarded headers ignored entirely
- `TRUSTED_PROXIES` set → headers honoured only from those addresses

If you must run uvicorn directly and are **not** behind a proxy, pass
`--no-proxy-headers`. If you are, pass `--forwarded-allow-ips` with your load
balancer's addresses — never leave it at the default.

| URL | Description |
|-----|-------------|
| `http://localhost:8000/feed` | Public flashcard feed (published cards only) |
| `http://localhost:8000/preview` | Reviewer visualizer — **requires reviewer credentials** |
| `http://localhost:8000/health` | Health check |
| `http://localhost:8000/docs` | Swagger UI (development only) |

---

## Testing

The test suite covers all major components with mocked external dependencies. No real API keys or network calls are required to run tests.

```bash
# Run all 23 tests
PYTHONPATH=. pytest -v
```

**Test coverage:**

| Test File | What It Tests |
|-----------|--------------|
| `test_client.py` | IndianKanoon client: search, fetch, budget guard, form input builder |
| `test_filters.py` | Pre-filter logic: blocklist, dedup, docsize soft check |
| `test_schemas.py` | Pydantic enum validation, SearchResult, StructuredSections |
| `test_phase3.py` | HTML structural parser, sanitizer truncation/redaction, AI Gate (mocked Groq), Card Generator (mocked Groq) |
| `test_phase4.py` | Full judgments pipeline orchestrator end-to-end (all dependencies mocked) |
| `test_phase6.py` | Supabase client placeholder detection, repository save + get_feed JSONL fallback |
| `test_feed_api.py` | Feed API: /health, /feed success, enum validation, limit clamping |
| `test_rights_pipeline.py` | Section text extractor, rights summarizer (mocked Groq), rights pipeline orchestrator |

Expected output:
```
======================== 23 passed in 2.18s =========================
```

---

## Roadmap

The following features are planned and designed but not yet implemented:

### Automated Weekly Ingestion (GitHub Actions)
- A `.github/workflows/weekly_ingestion.yml` cron job running every **Monday at 6:00 AM**
- Automatically runs the pipeline for all categories
- No manual intervention needed for regular content freshness

### On-Demand Reviewer Ingest Endpoint
- `POST /api/reviewer/ingest` — allows a reviewer to trigger a custom batch from the dashboard
- Parameters: `category`, `from_date`, `max_docs`
- Gives editorial teams direct control over what content enters the review queue

### Flutter In-App Reviewer Mode (LegalX_App)
- Toggle button inside the LegalX Flutter app to enter reviewer mode
- Floating bottom control bar: **[ 🟢 Approve ]**, **[ ✏️ Edit ]**, **[ 🔴 Reject ]**
- 3-tab Reviewer Dashboard:
  - **⏳ Pending Queue** — cards waiting for review
  - **🟢 Approved History** — audit log of approved cards (who approved, when)
  - **🔴 Rejected Archive** — rejected cards with reasons + ability to undo

### Database Audit Columns
The `shorts_cards` table will be extended with:
- `review_status`: `pending` | `approved` | `rejected`
- `reviewed_by`: Reviewer's User ID (UUID)
- `reviewed_at`: Exact timestamp of the review decision
- `rejection_reason`: Optional free-text note from the reviewer
