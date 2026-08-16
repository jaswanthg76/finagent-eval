# FinAgent Eval Python Code Flow

This guide explains how the backend executes at the Python function and persistence level. It is a
companion to the product and architecture discussion in the main README.

## 1. Application Entry Point

The backend starts in `backend/app/main.py`:

1. `FastAPI(...)` creates the application.
2. `CORSMiddleware` permits the configured frontend origin.
3. `app.include_router(api_router)` mounts all routes under `/api`.
4. `backend/app/api/router.py` combines the company, filing, metric, retrieval, and research routers.

Configuration is loaded once by `Settings` in `backend/app/core/config.py`. Database-backed route
functions receive an `AsyncSession` through `Depends(get_db)` from
`backend/app/core/database.py`. The dependency commits only where route code explicitly commits and
always closes the session after the request.

```text
HTTP request
  -> FastAPI route
  -> Pydantic request validation
  -> AsyncSession dependency
  -> application/retrieval/evaluation function
  -> SQLAlchemy model persistence
  -> Pydantic response serialization
```

## 2. SEC Filing Ingestion

### Metadata sync

`POST /api/companies/{ticker}/filings/sync` enters
`app.api.companies.sync_filings()`.

1. `_company_or_404()` resolves the fixed company row.
2. `SECClient.get_recent_filings()` downloads SEC submissions metadata and calls
   `parse_recent_filings()`.
3. The parser normalizes supported 10-K, 10-Q, and 8-K records.
4. New `Filing` models are inserted; existing accession numbers are not duplicated.

### Filing content

`POST /api/filings/{filing_id}/ingest` enters `app.api.filings.ingest_filing()` and then
`_ingest_filing_content()`.

1. `SECClient.get_filing_document()` downloads the primary filing HTML.
2. `parse_filing_html()` in `app.ingestion.filing_parser` converts HTML to text and extracts sections.
3. `chunk_text()` splits sections using model-token counts and overlap.
4. `FilingSection` and `FilingChunk` rows are recreated for that filing.
5. `Filing.ingested_at` marks successful completion.

Reingestion intentionally replaces derived sections and chunks. Any embedding tied to an old chunk
must therefore be regenerated.

### XBRL facts

`POST /api/companies/{ticker}/metrics/sync` enters
`app.api.metrics.sync_financial_metrics()`.

1. The SEC Company Facts payload is fetched.
2. `parse_company_facts()` in `app.ingestion.xbrl` maps supported SEC concepts to canonical metrics.
3. Normalized `FinancialMetric` rows retain value, unit, period, form, filing date, and accession.

## 3. Embedding and Retrieval Flow

`POST /api/companies/{ticker}/embeddings/sync` enters
`app.api.retrieval.sync_chunk_embeddings()`.

1. It selects company chunks whose embedding is missing, uses a different model, or has a stale
   `content_hash`.
2. `create_embedding_client()` selects `LocalEmbeddingClient` or `OpenAIEmbeddingClient` from
   configuration.
3. Text is embedded in bounded batches.
4. The vector, model name, content hash, and timestamp are persisted on each `FilingChunk`.

Semantic retrieval is centralized in `app.api.retrieval._semantic_search_for_company()`:

1. Confirm compatible embeddings exist.
2. Embed the query.
3. Calculate pgvector cosine distance.
4. Apply company, `as_of_date`, form, lower filing-date, and section filters in SQL.
5. Return ordered `SemanticSearchResult` objects with similarity and filing metadata.

The public `/search` route, hybrid research route, research agent, and contradiction checker all use
this constrained retrieval path.

## 4. Research Generation Flow

`POST /api/research` enters `app.api.research.generate_research_answer()`.

### Route preparation

1. `_company_or_404()` fixes the company from the requested ticker.
2. `identify_metric_names()` uses the canonical metric vocabulary to detect structured facts needed
   by the question.
3. Keyword heuristics decide whether to prefer recent filings or MD&A sections.
4. The route defines a request-scoped `execute_tool()` closure. The model cannot call SQL directly.

### Controlled tools

`execute_tool("search_filings", ...)` calls `_semantic_search_for_company()` with backend-enforced
company, form, and cutoff filters. Returned chunks receive stable report-local IDs `F1`, `F2`, and
so on.

`execute_tool("get_financial_metrics", ...)` calls `_metric_evidence()`. Returned facts receive
report-local IDs `M1`, `M2`, and so on. Python calculates absolute and percentage comparisons for
compatible periods; the model receives those calculations instead of doing arithmetic itself.

### Model loop

`GroqResearchAgent.answer()` in `app.research.agent`:

1. Builds the system prompt with ticker, cutoff, form, and required metrics.
2. Preloads two facts for each recognized metric.
3. Allows at most four validated tool-call rounds.
4. Returns invalid tool calls as structured tool errors for possible repair.
5. Forces a final answer after the bounded loop if the model has not finished.

### Immutable report snapshot

After generation, the route creates a `ResearchReport` containing:

- question, answer, provider, and model;
- exact tool calls;
- source descriptors;
- complete metric snapshots;
- complete filing-chunk snapshots;
- form and historical cutoff.

Later verification reads this saved snapshot rather than rerunning the original research query.

## 5. Claim Extraction

`POST /api/research/reports/{report_id}/claims/extract` enters
`app.api.research.extract_research_claims()`.

1. Existing ordered claims are returned unless `force=true`.
2. `GroqClaimExtractor.extract()` receives only the immutable answer and allowed report evidence IDs.
3. Its JSON response is validated as `ExtractedClaimsPayload`.
4. Unknown citations reject extraction; duplicate normalized claim text is removed.
5. `ResearchClaim` rows persist claim order, type, text, citation IDs, model, and prompt version.

Forced re-extraction replaces the report's claims. Claim-evaluation rows cascade with those claims,
and any cached `ReportEvaluation` is deleted.

## 6. Claim Verification

All claim checks are idempotent by `(claim_id, evaluation_type)`. Without `force=true`, a complete
existing set is returned. Reverification replaces only that evaluation type and invalidates the
cached report score.

### Numeric verification

`POST .../verify-numeric` calls `verify_report_numeric_claims()` and then
`app.evaluation.numeric.verify_numeric_claim()` for every eligible claim.

1. `extract_claimed_values()` parses explicit percentages and scaled/currency amounts.
2. Only cited `M#` snapshots are available to the verifier.
3. `_calculated_values()` derives metric values, absolute changes, and percentage changes in Python.
4. `_matches()` applies deterministic tolerances.
5. A `NUMERIC` `ClaimEvaluation` stores status, reason, confidence, inputs, calculated candidates,
   evidence IDs, and verifier version.

### Filing-citation verification

`POST .../verify-citations` calls `verify_report_citations()`.

1. Qualitative claims are paired only with their cited `F#` report snapshots.
2. Each claim is sent in an isolated evaluator call so evidence belonging to another claim is never
   present in its model context.
3. `compact_filing_evidence()` uses the shared `relevant_excerpt()` selector to keep the most
   claim-relevant window while bounding per-passage and total model context.
4. `GroqCitationEvaluator.evaluate()` distinguishes entailment, partial support, silence, and direct
   conflict.
5. Returned claim IDs must exactly match the requested IDs, and each result's material evidence IDs
   must be a subset of that claim's allowed citations.
6. A `CITATION` `ClaimEvaluation` stores the result and exact evaluated evidence snapshots.

Claims with no valid stored filing citations become `UNSUPPORTED` without a model call.

### Contradiction verification

`POST .../verify-contradictions` calls `verify_report_contradictions()`.

1. Each qualitative claim text becomes a fresh semantic query.
2. `is_contradiction_eligible()` excludes numeric claims and meta-claims about the report's evidence,
   citations, filing coverage, or evidence sufficiency.
3. Retrieval fixes the report company and reapplies `as_of_date`, while searching across filing forms.
4. Every filing chunk already in the report snapshot is excluded.
5. The route retrieves 12 candidates and retains up to four independent passages per claim.
6. `relevant_excerpt()` selects a bounded window using claim-term overlap, favoring rarer matching
   terms so relevant language beyond a chunk prefix remains visible.
7. Claims are evaluated in batches of at most three, keeping the evidence budget claim-specific.
8. Candidates receive persistent chunk-derived IDs such as `C123`.
9. `GroqContradictionEvaluator.evaluate()` distinguishes support, qualification, silence, and explicit
   contradiction while validating all returned claim and evidence IDs.
10. A `CONTRADICTION` `ClaimEvaluation` stores both materially cited IDs and every candidate snapshot
   actually shown to the evaluator.

No independent candidates produces an explicit `UNSUPPORTED` result. In scoring, that absence does
not override a normal numeric or citation result; only `CONTRADICTED` does.

### Temporal verification

`POST .../verify-temporal` calls `verify_report_temporal_integrity()` and the pure function
`app.evaluation.temporal.verify_temporal_integrity()`.

1. Rebuild report-local `M#` and `F#` lookup maps from saved snapshots.
2. Resolve every saved source.
3. Parse its filing date.
4. Compare it with `ResearchReport.as_of_date`.
5. Persist one `ReportTemporalEvaluation` with status, score, violations, reason, and version.

This check never calls a model. Missing snapshots, invalid dates, and post-cutoff sources are explicit
violations. A missing cutoff is `NOT_APPLICABLE`.

## 7. Reliability Scoring

`POST /api/research/reports/{report_id}/evaluate` enters `app.api.research.evaluate_report()`.

1. Load all report claims, typed claim evaluations, and the optional temporal evaluation.
2. Require `NUMERIC` checks for numeric-eligible claims.
3. Require `CITATION` and `CONTRADICTION` checks for qualitative claims.
4. Convert ORM rows into `ScoringClaim` and `ScoringEvaluation` dataclasses.
5. Call the pure `app.evaluation.scoring.calculate_report_score()` function.
6. Persist the resulting `ReportEvaluation` with `SCORING_VERSION`.

Component calculation is:

```text
grounding          = average CITATION statuses
numeric accuracy   = average NUMERIC statuses
citation coverage  = claims with at least one citation / all claims
temporal integrity = deterministic report-level result when applicable
```

Unavailable components remain `None`; included weights are renormalized. Report claim counts use the
worst ordinary numeric/citation status, except that a confirmed contradiction always overrides it.

## 8. Persistence Map

```text
Company
  -> Filing
       -> FilingSection
            -> FilingChunk + embedding
  -> FinancialMetric
  -> ResearchReport (immutable evidence snapshot)
       -> ResearchClaim
            -> ClaimEvaluation: NUMERIC
            -> ClaimEvaluation: CITATION
            -> ClaimEvaluation: CONTRADICTION
       -> ReportTemporalEvaluation
       -> ReportEvaluation
```

Database changes are defined in `backend/alembic/versions`. The current migration chain ends at
`20260816_0012`, which adds immutable evaluated-evidence snapshots to claim evaluations.

## 9. Error Translation

The API layer translates internal failures into deliberate HTTP responses:

- `404`: requested company, filing, or report does not exist;
- `409`: required embeddings or prerequisite evaluations are missing;
- `422`: filing content is invalid or cannot be parsed safely;
- `502`: configured external model/provider failed or returned invalid output;
- `503`: required provider credentials or embedding configuration is missing.

Provider-specific exceptions stay inside agent/evaluator modules. Route functions expose concise
errors and avoid persisting partial evaluation batches.

## 10. Where to Add the Next Improvements

- Query planning and hybrid ranking: `app/api/retrieval.py` or a new retrieval service module.
- More numeric expressions: `app/evaluation/numeric.py` plus focused unit tests.
- Contradiction query expansion: before `_semantic_search_for_company()` in
  `verify_report_contradictions()`; keep cutoff enforcement in the shared retrieval function.
- New score strategy: add a versioned implementation beside `app/evaluation/scoring.py` rather than
  changing previously persisted score semantics silently.
- Background execution: move route orchestration into service/task functions; keep FastAPI routes as
  validation and job-submission boundaries.
- Relational evidence lineage: add evaluation-evidence models and migrations while retaining JSON
  snapshots for reproducibility.
