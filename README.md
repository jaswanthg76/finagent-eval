# FinAgent Eval

AI reliability infrastructure for financial research.

FinAgent Eval ingests public SEC filings and structured XBRL financial data, generates grounded company research, breaks that research into atomic claims, and independently verifies those claims against primary-source evidence.

The goal is **not** to predict stock prices or build another financial chatbot.

The core idea is:

> **Evaluate and verify autonomous financial research agents using public company filings as ground truth.**

---

## Why This Exists

Financial research agents can generate useful analysis quickly, but their outputs can still contain:

- unsupported claims,
- incorrect financial calculations,
- weak or missing citations,
- contradictory conclusions,
- stale information,
- and accidental use of information published after the requested historical date.

FinAgent Eval adds a verification layer after research generation.

A user can ask:

```text
What changed in NVIDIA's growth thesis over the last two quarters?
```

The system generates a research report and then independently checks each important claim.

Example:

```text
Research Reliability: 92 / 100

Grounding             94%
Numerical Accuracy   100%
Citation Coverage     88%
Temporal Integrity   100%

✓ Revenue growth claim verified
✓ Margin decline supported
⚠ Supply constraint claim partially supported
✗ One unsupported claim
```

---

# V1 Scope

Initial companies:

- NVDA
- AMD
- MSFT
- META
- GOOGL

Initial data sources:

- SEC 10-K filings
- SEC 10-Q filings
- SEC 8-K filings
- SEC XBRL / Company Facts data

Not included in V1:

- stock price prediction,
- news ingestion,
- social media,
- paid market-data providers,
- third-party earnings-call transcripts,
- Bloomberg,
- FactSet,
- Capital IQ.

The first version should prove one core loop:

```text
SEC data
   ↓
Research question
   ↓
Generated report
   ↓
Extract claims
   ↓
Verify claims
   ↓
Reliability score
```

---

# Tech Stack

## Frontend

- React
- Vite
- TypeScript
- Tailwind CSS
- shadcn/ui
- TanStack Table
- Recharts

Deployment:

- Vercel

The frontend is intentionally client-side only.

FastAPI owns all backend APIs and application logic.

---

## Backend

- Python
- FastAPI

Responsibilities:

- SEC ingestion,
- XBRL ingestion,
- document parsing,
- retrieval,
- financial metric queries,
- research orchestration,
- claim extraction,
- claim verification,
- scoring,
- background-job management.

---

## Database

- PostgreSQL
- pgvector

PostgreSQL stores:

- companies,
- filings,
- filing sections,
- structured financial metrics,
- research reports,
- claims,
- evidence,
- evaluations.

pgvector stores embeddings for filing chunks.

A separate vector database is not required for V1.

---

## Background Processing

- Redis
- Celery

Used for:

- filing ingestion,
- parsing,
- embedding generation,
- report generation,
- report evaluation.

Heavy work should not block HTTP requests.

---

## AI

Model provider should remain configurable.

Possible providers:

- Gemini
- OpenAI

The application should avoid coupling core business logic to a specific model vendor.

---

# High-Level Architecture

```text
                               USER
                                 │
                                 ▼
                       ┌──────────────────┐
                       │   React + Vite   │
                       │     Frontend     │
                       └────────┬─────────┘
                                │ REST
                                ▼
                       ┌──────────────────┐
                       │     FastAPI      │
                       │     Backend      │
                       └────────┬─────────┘
                                │
             ┌──────────────────┼──────────────────┐
             │                  │                  │
             ▼                  ▼                  ▼
       Research Engine    Evaluation Engine    Ingestion
             │                  │                  │
      ┌──────┴──────┐     ┌─────┴───────┐    ┌────┴────┐
      │             │     │             │    │         │
      ▼             ▼     ▼             ▼    ▼         ▼
 Semantic       Financial Claim       Claim  Filing    XBRL
 Retrieval       Metrics   Extractor   Verifier Parser Normalizer
      │             │        │             │    │         │
      └──────┬──────┘        └──────┬──────┘    └────┬────┘
             │                      │                │
             └──────────────────────┼────────────────┘
                                    │
                                    ▼
                         ┌────────────────────┐
                         │ PostgreSQL         │
                         │ + pgvector         │
                         └────────────────────┘
                                    ▲
                                    │
                               Redis/Celery
                                    ▲
                                    │
                               Background
                                 Workers


External data:

SEC EDGAR ───────────────► Filing ingestion
SEC XBRL / Company Facts ► Financial metrics
```

---

# Architecture Principles

The project should begin as a **modular monolith**.

Do not introduce unnecessary microservices.

The backend should have clear internal boundaries between:

1. ingestion,
2. retrieval,
3. research generation,
4. evaluation,
5. persistence.

The following rules are important:

1. SEC ingestion must not depend on an LLM.
2. Structured financial calculations must not depend on an LLM.
3. Historical filtering must not depend on an LLM.
4. Retrieval logic must remain separate from agent logic.
5. Evaluation logic must remain separate from research generation.
6. Source metadata must be preserved throughout the pipeline.
7. Every evaluated claim should be traceable to evidence.
8. Ingestion must be idempotent.
9. Deterministic checks should remain deterministic.
10. LLMs should only be used where semantic reasoning is actually required.

---

# Project Structure

Recommended repository layout:

```text
finagent-eval/
│
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── features/
│   │   ├── hooks/
│   │   ├── lib/
│   │   ├── services/
│   │   └── types/
│   │
│   ├── package.json
│   └── vite.config.ts
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── models/
│   │   ├── schemas/
│   │   ├── repositories/
│   │   ├── ingestion/
│   │   ├── retrieval/
│   │   ├── research/
│   │   ├── evaluation/
│   │   ├── workers/
│   │   ├── core/
│   │   └── main.py
│   │
│   ├── tests/
│   └── pyproject.toml
│
├── docker-compose.yml
├── .env.example
└── README.md
```

Suggested backend layout:

```text
backend/app/

├── api/
│   ├── companies.py
│   ├── filings.py
│   └── research.py
│
├── ingestion/
│   ├── sec_client.py
│   ├── filing_parser.py
│   ├── chunker.py
│   ├── embeddings.py
│   └── xbrl.py
│
├── retrieval/
│   ├── semantic.py
│   └── financial.py
│
├── research/
│   ├── agent.py
│   ├── prompts.py
│   └── tools.py
│
├── evaluation/
│   ├── claim_extractor.py
│   ├── numeric.py
│   ├── grounding.py
│   ├── temporal.py
│   ├── contradiction.py
│   └── scoring.py
│
├── models/
├── schemas/
├── repositories/
├── workers/
├── core/
└── main.py
```

---

# Data Ingestion

There are two independent ingestion paths.

## 1. Filing Text

```text
SEC EDGAR
    ↓
10-K / 10-Q / 8-K
    ↓
Fetch filing
    ↓
Parse readable content
    ↓
Extract sections
    ↓
Create chunks
    ↓
Generate embeddings
    ↓
Store in PostgreSQL + pgvector
```

Each filing should preserve metadata such as:

```json
{
  "ticker": "NVDA",
  "form_type": "10-Q",
  "accession_number": "...",
  "filing_date": "2026-05-20",
  "period_end": "2026-04-26",
  "source_url": "..."
}
```

SEC accession number should be unique.

Running ingestion multiple times must not create duplicate records.

---

## 2. XBRL Financial Data

```text
SEC Company Facts / XBRL
          ↓
       Normalize
          ↓
 financial_metrics
          ↓
      PostgreSQL
```

Example normalized values:

```text
NVDA
Revenue
2026 Q1
44.1B

NVDA
Revenue
2025 Q1
26.0B
```

Structured metrics should be used whenever possible instead of asking an LLM to infer values from text.

---

# Raw Filing Storage

Raw SEC HTML is **optional for V1**.

The application should not depend on raw-file storage during normal research queries.

The primary application data is:

- parsed filing metadata,
- cleaned sections,
- filing chunks,
- embeddings,
- structured XBRL metrics.

If raw filings are retained, they should be treated only as a reprocessing/audit source.

For the leanest V1, storing the original SEC URL plus parsed content is sufficient.

---

# Core Database Entities

## companies

```text
id
ticker
name
cik
sector
created_at
updated_at
```

---

## filings

```text
id
company_id
accession_number
form_type
filing_date
period_end
source_url
created_at
```

Recommended constraints:

```text
UNIQUE(accession_number)
```

---

## filing_sections

```text
id
filing_id
section_name
section_order
content
created_at
```

Typical sections:

- Business
- Risk Factors
- MD&A
- Financial Statements
- Liquidity
- Segment Results

---

## filing_chunks

```text
id
filing_id
section_id
content
chunk_index
embedding
token_count
created_at
```

`embedding` should use pgvector.

---

## financial_metrics

```text
id
company_id
filing_id
metric_name
xbrl_tag
value
unit
period_start
period_end
filing_date
fiscal_year
fiscal_period
created_at
```

Preserve both:

- normalized metric name,
- original XBRL tag.

---

## research_reports

```text
id
company_id
question
as_of_date
form
answer
provider
model
tool_calls (JSONB)
sources (JSONB)
metrics (JSONB)
chunks (JSONB)
created_at
```

Each report stores an immutable snapshot of the model/tool trace and supporting evidence used when
the answer was generated. Reopening a report does not rerun retrieval, so its citations remain
auditable even if filings, embeddings, or retrieval behavior later change.

---

## research_claims

```text
id
report_id
claim_index
claim_text
claim_type
citation_ids (JSONB)
extraction_metadata (JSONB)
created_at
```

Possible claim types:

```text
NUMERIC
FACTUAL
MANAGEMENT_STATEMENT
COMPARATIVE
TEMPORAL
OTHER
```

Claims should be atomic.

Example:

```text
"Revenue increased 40% while demand remained supply constrained."
```

should become:

```text
Claim 1:
Revenue increased 40%.

Claim 2:
Demand remained supply constrained.
```

---

## claim_sources

```text
id
claim_id
filing_id
chunk_id
support_type
relevance_score
created_at
```

This table links claims back to evidence.

---

## claim_evaluations

```text
id
claim_id
evaluation_type
status
confidence
reason
evidence_ids
claimed_values
calculated_values
verifier_version
created_at
```

Statuses:

```text
VERIFIED
PARTIALLY_SUPPORTED
UNSUPPORTED
CONTRADICTED
ERROR
```

---

## report_evaluations

```text
id
report_id
overall_score
grounding_score
numeric_accuracy_score
citation_score
temporal_integrity_score
unsupported_claim_count
contradiction_count
created_at
```

---

# Retrieval

FinAgent Eval uses **two different retrieval systems**.

This distinction is important.

---

## Semantic Retrieval / RAG

Used for qualitative questions.

Examples:

```text
What did management say about AI demand?

Why did margins decline?

What risks did management highlight?
```

Flow:

```text
Question
   ↓
Generate query embedding
   ↓
pgvector similarity search
   ↓
Relevant filing chunks
   ↓
Research agent
```

This is Retrieval-Augmented Generation.

---

## Structured Financial Retrieval

Used for quantitative questions.

Examples:

```text
What was revenue?

How much did revenue grow YoY?

What was EPS?

How did operating margin change?
```

Flow:

```text
Question
   ↓
Identify metric
   ↓
Query financial_metrics
   ↓
Calculate result programmatically
```

Do not use vector search when structured financial data already answers the question.

---

# Research Agent

The research agent should not query database tables directly.

It should receive a controlled set of tools.

Initial tools:

```text
search_filings()
get_filings()
get_financial_metric()
compare_financial_metrics()
get_company_information()
```

Example:

```text
search_filings(
    ticker="NVDA",
    query="gross margin decline",
    as_of_date="2026-05-30",
    limit=8
)
```

Example:

```text
compare_financial_metrics(
    ticker="NVDA",
    metric="Revenue",
    current_period="2026Q1",
    comparison_period="2025Q1",
    as_of_date="2026-05-30"
)
```

Research flow:

```text
User Query
    ↓
Agent determines needed evidence
    ↓
Semantic retrieval and/or metric lookup
    ↓
Generate research report
    ↓
Attach source references
    ↓
Persist report
```

---

# Historical / As-Of-Date Research

Every research request may include:

```text
as_of_date
```

Example:

```text
ticker = NVDA
as_of_date = 2025-01-01
```

All retrieval must enforce:

```text
filing_date <= as_of_date
```

This rule belongs in the retrieval/data layer.

Do not rely on prompt instructions such as:

```text
"Do not use future data."
```

The LLM should never receive future documents in the first place.

This enables historical research without look-ahead leakage.

---

# Evaluation Pipeline

The evaluation pipeline is the main differentiating component.

After research generation:

```text
Research Report
      ↓
Claim Extraction
      ↓
Atomic Claims
      ↓
Verification
      ↓
Reliability Score
```

Each claim should be evaluated independently.

---

# Numeric Verification

Example claim:

```text
Revenue grew 69% year over year.
```

Extract:

```text
metric = revenue
operation = YoY growth
claimed_value = 69%
```

Then:

```text
financial_metrics
       ↓
retrieve current value
retrieve comparison value
       ↓
calculate in Python
       ↓
compare with claimed result
```

Example:

```text
Claimed:    69.0%
Calculated: 68.7%

✓ VERIFIED
```

Simple arithmetic must never be delegated to an LLM.

---

# Grounding Verification

Example claim:

```text
Management expects AI demand to remain strong.
```

Flow:

```text
Claim
  ↓
Retrieve likely supporting chunks
  ↓
Retrieve relevant context
  ↓
Semantic evaluator
  ↓
SUPPORTED / PARTIAL / UNSUPPORTED / CONTRADICTED
```

An LLM may be used here because semantic interpretation is required.

The supporting evidence used for evaluation must always be persisted.

---

# Temporal Verification

Temporal integrity should be deterministic.

For every source used by a report:

```text
source.filing_date <= report.as_of_date
```

If a report references information published after the requested historical date:

```text
temporal_integrity = FAILED
```

No LLM is required for this check.

---

# Contradiction Detection

For important qualitative claims, retrieve both:

```text
supporting evidence
```

and:

```text
potentially contradictory evidence
```

Example:

```text
Claim:
Demand remains supply constrained.

Supporting evidence:
...

Potential contradiction:
Management says supply availability improved materially.
```

The semantic evaluator then determines whether the claim is:

```text
VERIFIED
PARTIALLY_SUPPORTED
UNSUPPORTED
CONTRADICTED
```

---

# Reliability Scoring

Keep the first scoring model simple.

Example:

```text
overall_score =
    grounding_score * 0.35
  + numeric_accuracy_score * 0.30
  + citation_score * 0.20
  + temporal_integrity_score * 0.15
```

Store every component separately.

Do not hard-code assumptions throughout the application.

The scoring strategy should be easy to replace later.

---

# Example API Response

```json
{
  "ticker": "NVDA",
  "query": "What changed in NVIDIA's growth thesis?",
  "as_of_date": "2026-05-30",
  "report": "...",
  "evaluation": {
    "overall_score": 92,
    "grounding_score": 94,
    "numeric_accuracy_score": 100,
    "citation_score": 88,
    "temporal_integrity_score": 100,
    "unsupported_claims": 2,
    "contradictions": 0
  }
}
```

Individual claim:

```json
{
  "claim": "Revenue increased 69% year over year.",
  "type": "NUMERIC",
  "status": "VERIFIED",
  "claimed_value": 69.0,
  "calculated_value": 68.7,
  "source": {
    "form_type": "10-Q",
    "filing_date": "2026-05-20"
  }
}
```

---

# Background Jobs

Initial Celery jobs:

```text
ingest_company
ingest_filing
parse_filing
generate_embeddings
sync_xbrl
generate_research_report
evaluate_research_report
```

Example request flow:

```text
POST /research
       │
       ▼
    FastAPI
       │
       ▼
  Celery Queue
       │
       ▼
     Worker
       │
       ├── retrieve data
       ├── generate report
       ├── extract claims
       ├── evaluate claims
       └── score report
       │
       ▼
   PostgreSQL
```

The frontend can poll report status for V1.

WebSockets are unnecessary initially.

---

# Initial API Surface

Keep the API small.

```text
GET  /api/companies
GET  /api/companies/{ticker}

GET  /api/companies/{ticker}/filings
POST /api/companies/{ticker}/filings/sync
GET  /api/companies/{ticker}/metrics

POST /api/filings/{filing_id}/ingest
GET  /api/filings/{filing_id}/sections

POST /api/research
GET  /api/research/reports
GET  /api/research/reports/{report_id}
POST /api/research/reports/{report_id}/claims/extract
GET  /api/research/reports/{report_id}/claims
POST /api/research/reports/{report_id}/verify-numeric
POST /api/research/reports/{report_id}/verify-citations
GET  /api/research/reports/{report_id}/evaluations

Planned report-level evaluation endpoints:

POST /api/research/reports/{report_id}/evaluate
GET  /api/research/reports/{report_id}/evaluation
```

---

# Frontend

Start from:

```text
React
+
Vite
+
TypeScript
+
shadcn/ui
```

A dashboard-style UI is preferred over a consumer stock-trading interface.

The product should feel like an analyst/research tool.

---

## Main Screens

### 1. Companies

Display initial supported companies:

```text
NVDA
AMD
MSFT
META
GOOGL
```

---

### 2. Research

Inputs:

```text
Company

Question

As-of date
```

Example:

```text
Company:
NVDA

Question:
What changed in NVIDIA's data-center growth thesis
over the previous two quarters?

As of:
2026-05-30
```

---

### 3. Research Report

Display:

- generated report,
- source references,
- relevant filings,
- financial metrics used.

---

### 4. Evaluation

Example:

```text
Research Reliability
92 / 100

Grounding             94%
Numerical Accuracy   100%
Citation Coverage     88%
Temporal Integrity   100%
```

Below the score, display the claims table:

```text
CLAIM                              STATUS                 SOURCE

Revenue grew 69%                   ✓ Verified             Q1 10-Q
Margins declined                   ✓ Verified             Q1 10-Q
Demand remains supply constrained ⚠ Partially Supported  MD&A
CUDA switching costs increased     ✗ Unsupported          —
```

Selecting a claim should reveal:

- source filing,
- source section,
- exact evidence,
- evaluator reasoning,
- calculated values for numeric claims.

---

# Frontend Deployment

Frontend:

```text
React + Vite
      ↓
    Vercel
```

Environment variable:

```text
VITE_API_URL=https://api.example.com
```

Frontend requests should go directly to FastAPI.

Do not introduce Next.js API routes or another backend layer.

---

# Backend Deployment

The backend can initially run on any container-compatible platform.

Example:

```text
Frontend
finagent.example.com
        │
        ▼
      Vercel


Backend
api.finagent.example.com
        │
        ▼
      FastAPI
        │
  ┌─────┴─────┐
  ▼           ▼
Postgres     Redis
pgvector     Celery
```

Dockerize the FastAPI application and Celery worker.

---

# Development Environment

Recommended local services:

```text
frontend
backend
worker
postgres
redis
```

Use Docker Compose for PostgreSQL and Redis.

The frontend and FastAPI application may run directly during development for faster reload cycles.

Start PostgreSQL and Redis, then launch both application servers:

```bash
docker compose up -d
./dev.sh
```

The development script applies pending Alembic migrations before starting FastAPI. The frontend is
available at `http://localhost:5173`, the API at `http://localhost:8000`, and interactive API docs at
`http://localhost:8000/docs`.

To apply migrations manually:

```bash
cd backend
uv run alembic upgrade head
```

---

# Environment Variables

Example `.env.example`:

```text
DATABASE_URL=

REDIS_URL=

AI_PROVIDER=groq
AI_MODEL=llama-3.3-70b-versatile
GROQ_API_KEY=

EMBEDDING_PROVIDER=local
EMBEDDING_MODEL=jinaai/jina-embeddings-v2-small-en
EMBEDDING_DIMENSIONS=512
EMBEDDING_BATCH_SIZE=32

SEC_USER_AGENT=

FRONTEND_URL=http://localhost:5173
```

Frontend:

```text
VITE_API_URL=http://localhost:8000
```

Never commit secrets.

---

# Important SEC Requirement

Automated SEC requests should use an identifiable User-Agent and respect SEC fair-access rules.

SEC ingestion should include:

- rate limiting,
- retries,
- timeout handling,
- idempotency,
- local persistence of parsed results.

The application should not hit SEC EDGAR on every research request.

Research should run against the locally stored dataset.

---

# V1 Implementation Order

Build in this order:

```text
1. Repository setup

2. PostgreSQL + pgvector

3. Company schema

4. SEC filing metadata ingestion

5. Filing text parser

6. Filing section extraction

7. Chunking

8. Embedding generation

9. pgvector semantic search

10. XBRL financial metric ingestion

11. Structured financial retrieval

12. Research tools

13. Basic research agent

14. Report persistence

15. Claim extraction

16. Numeric verification

17. Grounding verification

18. Temporal verification

19. Contradiction detection

20. Reliability scoring

21. React dashboard

22. Vercel frontend deployment

23. Backend deployment
```

---

# Current Prototype Scope

The current implementation is intentionally a research prototype, not a production financial
advice system.

It currently supports:

- syncing SEC filing metadata and document content,
- extracting filing sections and creating model-token-based chunks,
- ingesting normalized SEC Company Facts / XBRL metrics,
- generating local embeddings and searching filing chunks with pgvector,
- hybrid retrieval of qualitative filing passages and quantitative facts,
- a Groq-hosted research agent with controlled retrieval tools,
- exact `ticker`, filing-form, and `as_of_date` filtering,
- deterministic comparable-period selection and financial calculations,
- compact two-fact metric preloads for comparable-period questions on free-tier model limits,
- cited answers that retain links to the underlying SEC filings,
- persistent research reports with immutable tool and evidence snapshots,
- report-history and report-reopen APIs scoped by company,
- guarded atomic-claim extraction with typed claims and citation allowlist validation,
- persisted, ordered claims that are idempotently reused after extraction,
- deterministic numeric-claim verification against cited structured metric evidence,
- persisted numeric evaluations with claimed values, calculated values, reasons, and verifier versions,
- bounded qualitative entailment checks against only each claim's cited filing passages,
- coexisting typed numeric and citation evaluations with audited evidence IDs and verifier versions,
- UI actions for inspecting evidence, generating or reopening research, extracting claims, and
  verifying numeric claims or filing citations.

## Deliberately Deferred

The following work is valuable, but was skipped so the prototype could validate the core ingestion,
retrieval, and grounded-generation flow first:

- multi-turn conversation history,
- relational `claim_sources` records instead of citation IDs stored only as JSON,
- independent temporal and cross-filing contradiction verification,
- claim-level and report-level reliability scores,
- human review and approval workflows,
- authentication, authorization, tenants, organizations, and usage quotas,
- durable background-job orchestration, retries, dead-letter handling, and job monitoring,
- production observability, tracing, model-cost tracking, and alerting,
- prompt and model versioning with regression evaluation datasets,
- response streaming, cancellation, and long-running research jobs,
- broader test coverage against malformed filings, XBRL restatements, and provider failures,
- production deployment hardening, backups, retention policies, and disaster recovery,
- licensed transcripts, market data, news, investor presentations, and other non-SEC sources.

Numeric verification covers explicit percentages and scaled or currency amounts when a claim cites
structured metric evidence. Qualitative verification separately checks atomic claims against their
cited filing excerpts. Generated answers should still be treated as assisted research: these checks
are bounded prototype controls, not a guarantee, and users should open the attached SEC evidence
before relying on a material claim.

---

# AI Grounding and Hallucination Guardrails

These controls reduce hallucination risk; they do not prove that every generated sentence is true.

## Controls Implemented in the Prototype

### Controlled Tool Access

The model cannot query database tables or choose an arbitrary company. It can request only the
application-defined tools:

```text
search_filings
get_financial_metrics
```

The backend fixes the company from the research request, validates all tool arguments, rejects extra
arguments, restricts metrics to a canonical allowlist, and enforces small result limits.

### Data-Layer Scope Enforcement

Company, filing type, and historical cut-off filters are applied in SQL before evidence is returned
to the model:

```text
company_id = selected company
form = requested form
filing_date <= as_of_date
```

For questions about recent results, filing retrieval is restricted to recent filings. Questions
about causes or drivers prefer Management's Discussion and Analysis sections. The prompt is not
trusted to enforce these boundaries.

### Structured Quantitative Path

Explicit financial terms such as revenue or diluted EPS trigger structured metric retrieval before
generation. The model does not need to infer numbers from prose.

Comparable-period selection prefers facts with the same fiscal period and similar duration.
Absolute and percentage changes are calculated by backend code and supplied to the model as
`deterministic_comparisons`; the model is instructed not to recalculate them.

### Evidence-Preserving Generation

Every returned fact or filing passage receives an evidence ID:

```text
[M1] structured financial metric
[F1] SEC filing passage
```

The model is instructed to cite only these IDs, avoid unsupported values and periods, distinguish
narrative drivers from coincidental movements, and state when evidence is insufficient. The API
returns the complete evidence objects and SEC URLs alongside the answer so the UI can expose the
primary sources independently of the generated prose.

### Bounded Agent Execution

Agent execution uses temperature-zero generation, a bounded tool-call loop, validated schemas,
compact model-facing excerpts, and fixed evidence limits. These controls reduce uncontrolled
behavior, latency, token usage, and the opportunity for irrelevant context to influence the answer.

### Guarded Atomic Claim Extraction

Claim extraction operates only on the immutable saved answer and its known evidence IDs. It uses a
strict JSON schema, a six-value claim-type enum, a 30-claim limit, duplicate removal, stable claim
ordering, and an evidence-ID allowlist. Unknown citations cause extraction to fail instead of being
stored. Existing claims are returned idempotently, avoiding repeated model calls unless a future
explicit re-extraction is requested.

Extraction separates assertions for later verification; it does not itself prove that a claim is
correct or that its citations entail it.

### Deterministic Numeric Claim Verification

Eligible atomic claims are independently parsed for explicit percentages and scaled or currency
amounts. The verifier reads only the claim's cited `[M#]` facts from the immutable report snapshot,
recalculates absolute and percentage changes in backend code, and compares the claimed values with
small documented tolerances. It does not use an LLM for arithmetic.

Each result is persisted idempotently as `VERIFIED`, `PARTIALLY_SUPPORTED`, `UNSUPPORTED`,
`CONTRADICTED`, or `ERROR`, along with the parsed values, calculated candidates, reason, confidence,
and verifier version. The UI displays these results beside their claims. A valid numeric result does
not prove that a qualitative citation entails the surrounding claim.

### Bounded Filing-Citation Entailment

Qualitative claims are evaluated in a separate temperature-zero model call using only the atomic
claim and its cited `[F#]` passages from the immutable report snapshot. Evidence excerpts have fixed
per-passage and total-size limits. The evaluator must distinguish explicit support, partial support,
silence, and contradiction; semantic similarity alone is explicitly insufficient.

The response is schema-validated, must return exactly the requested claim IDs, and cannot introduce
unknown evidence. Claims without stored filing citations are marked `UNSUPPORTED` without a model
call. Results are persisted independently from numeric evaluations, including the assessed evidence
IDs, reason, confidence, and verifier version, and are displayed alongside each atomic claim.

## Guardrails Still Needed for Production

The prototype now performs independent numeric recalculation and bounded qualitative entailment
checks, but an evaluator model can still misunderstand a passage or share biases with the generation
model. Truncated excerpts can also omit relevant context, and the system does not yet independently
reapply every temporal constraint or search other filings for contradictions.

The production verification layer should therefore:

1. Expand numeric parsing to ratios, per-share values, ranges, and unusual units.
2. Reapply temporal constraints independently of generation.
3. Search for counterevidence and contradictions within the answer and across filings.
4. Add evaluator-model diversity and regression datasets to measure correlated errors.
5. Assign an overall reliability score from the independent claim checks.
6. Require human review for high-impact or low-confidence outputs.

The goal is not to claim that the LLM cannot hallucinate. The goal is to constrain what it can see,
preserve auditable evidence, and eventually verify its output independently.

---

# Do Not Build Yet

Avoid prematurely adding:

- MCP server,
- thesis tracking,
- multi-agent workflows,
- Kubernetes,
- Kafka,
- separate vector databases,
- WebSockets,
- stock-price ingestion,
- news aggregation,
- auth complexity,
- organization/workspace management,
- portfolio management,
- paid financial APIs.

These can be added after the core verification loop works.

---

# Future Extensions

Once the V1 is stable:

## Thesis Tracking

Store an investment thesis:

```text
NVDA Thesis

1. AI accelerator demand remains strong
2. Hyperscaler capex remains elevated
3. Gross margins remain durable
4. CUDA remains a meaningful moat
```

New filings can then update each thesis point:

```text
AI demand            ↑ Stronger
Hyperscaler spending ↑ Stronger
Margins              ↓ Weaker
CUDA moat            → Unchanged
```

---

## MCP

Expose research and verification functionality through an MCP server.

Potential tools:

```text
search_filings
get_financial_metric
compare_financial_metrics
generate_research
evaluate_report
verify_claim
get_report_evaluation
```

MCP is an extension, not a V1 dependency.

---

## Additional Data Sources

Possible later integrations:

- company investor-relations pages,
- earnings releases,
- investor presentations,
- licensed transcripts,
- market prices,
- news,
- additional regulatory filings.

The system should continue distinguishing trusted source types and preserving source provenance.

---

# Definition of Done for V1

V1 is successful when the following demo works end to end:

1. Select NVDA.
2. Choose an `as_of_date`.
3. Ask a research question.
4. The system retrieves only SEC data available before that date.
5. The agent produces a sourced research report.
6. The report is decomposed into atomic claims.
7. Numeric claims are independently recalculated from structured financial data.
8. Qualitative claims are checked against filing evidence.
9. Future-data leakage is detected deterministically.
10. Every evaluation links back to its evidence.
11. The UI displays an overall reliability score and claim-level results.

If this loop works well, the core product works.

---

# Product Principle

The research agent is useful.

The **verification layer is the product**.

FinAgent Eval should not merely produce convincing financial analysis.

It should answer the more important question:

> **Why should I trust this analysis?**
