import { useEffect, useState, type FormEvent } from 'react'
import './App.css'

type Company = {
  id: number
  ticker: string
  name: string
  sec_cik: string
  created_at: string
}

type Filing = {
  id: number
  company_id: number
  accession_number: string
  form: string
  filing_date: string
  report_date: string | null
  primary_document: string
  document_url: string
  ingested_at: string | null
  created_at: string
}

type FilingSyncResult = {
  ticker: string
  fetched: number
  created: number
}

type FilingIngestResult = {
  filing_id: number
  sections_created: number
  chunks_created: number
  ingested_at: string
}

type FilingsReingestResult = {
  total: number
  succeeded: number
  failed: number
  results: FilingIngestResult[]
  failures: Array<{
    filing_id: number
    detail: string
  }>
}

type BulkIngestProgress = {
  completed: number
  total: number
  failed: number
}

type ChunkEmbeddingSyncResult = {
  ticker: string
  total_chunks: number
  eligible: number
  embedded: number
  skipped: number
  remaining: number
  model: string
  dimensions: number
}

type EmbeddingProgress = {
  completed: number
  total: number
  remaining: number
  model: string | null
}

type SemanticSearchResult = {
  chunk_id: number
  filing_id: number
  accession_number: string
  form: string
  filing_date: string
  report_date: string | null
  section_name: string
  chunk_index: number
  content: string
  token_count: number
  similarity: number
  source_url: string
  embedded_at: string
}

const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

async function responseError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: string }
    return body.detail ?? `API request failed with status ${response.status}`
  } catch {
    return `API request failed with status ${response.status}`
  }
}

function App() {
  const [companies, setCompanies] = useState<Company[]>([])
  const [selectedCompany, setSelectedCompany] = useState<Company | null>(null)
  const [filings, setFilings] = useState<Filing[]>([])
  const [companiesError, setCompaniesError] = useState<string | null>(null)
  const [filingsError, setFilingsError] = useState<string | null>(null)
  const [syncMessage, setSyncMessage] = useState<string | null>(null)
  const [isCompaniesLoading, setIsCompaniesLoading] = useState(true)
  const [isFilingsLoading, setIsFilingsLoading] = useState(false)
  const [isSyncing, setIsSyncing] = useState(false)
  const [isReingestingAll, setIsReingestingAll] = useState(false)
  const [isEmbeddingSyncing, setIsEmbeddingSyncing] = useState(false)
  const [ingestingFilingId, setIngestingFilingId] = useState<number | null>(null)
  const [bulkProgress, setBulkProgress] = useState<BulkIngestProgress | null>(null)
  const [embeddingProgress, setEmbeddingProgress] = useState<EmbeddingProgress | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [searchForm, setSearchForm] = useState('')
  const [searchAsOfDate, setSearchAsOfDate] = useState('')
  const [searchResults, setSearchResults] = useState<SemanticSearchResult[]>([])
  const [searchError, setSearchError] = useState<string | null>(null)
  const [isSearching, setIsSearching] = useState(false)
  const [hasSearched, setHasSearched] = useState(false)

  useEffect(() => {
    const controller = new AbortController()

    async function loadCompanies() {
      try {
        const response = await fetch(`${API_URL}/api/companies`, {
          signal: controller.signal,
        })
        if (!response.ok) throw new Error(await responseError(response))

        const data = (await response.json()) as Company[]
        setCompanies(data)
        setSelectedCompany(data.find((company) => company.ticker === 'NVDA') ?? data[0] ?? null)
      } catch (requestError) {
        if (requestError instanceof Error && requestError.name !== 'AbortError') {
          setCompaniesError(requestError.message)
        }
      } finally {
        if (!controller.signal.aborted) setIsCompaniesLoading(false)
      }
    }

    void loadCompanies()
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (!selectedCompany) return

    const controller = new AbortController()
    setIsFilingsLoading(true)
    setFilingsError(null)
    setSyncMessage(null)
    setSearchResults([])
    setSearchError(null)
    setHasSearched(false)

    async function loadFilings() {
      try {
        const response = await fetch(
          `${API_URL}/api/companies/${selectedCompany?.ticker}/filings`,
          { signal: controller.signal },
        )
        if (!response.ok) throw new Error(await responseError(response))
        setFilings((await response.json()) as Filing[])
      } catch (requestError) {
        if (requestError instanceof Error && requestError.name !== 'AbortError') {
          setFilingsError(requestError.message)
        }
      } finally {
        if (!controller.signal.aborted) setIsFilingsLoading(false)
      }
    }

    void loadFilings()
    return () => controller.abort()
  }, [selectedCompany])

  async function syncFilings() {
    if (!selectedCompany) return

    setIsSyncing(true)
    setFilingsError(null)
    setSyncMessage(null)
    try {
      const syncResponse = await fetch(
        `${API_URL}/api/companies/${selectedCompany.ticker}/filings/sync`,
        { method: 'POST' },
      )
      if (!syncResponse.ok) throw new Error(await responseError(syncResponse))
      const result = (await syncResponse.json()) as FilingSyncResult

      const filingsResponse = await fetch(
        `${API_URL}/api/companies/${selectedCompany.ticker}/filings`,
      )
      if (!filingsResponse.ok) throw new Error(await responseError(filingsResponse))
      setFilings((await filingsResponse.json()) as Filing[])
      setSyncMessage(
        result.created > 0
          ? `Added ${result.created} new filings from SEC EDGAR.`
          : `Already up to date with ${result.fetched} SEC filings.`,
      )
    } catch (requestError) {
      if (requestError instanceof Error) setFilingsError(requestError.message)
    } finally {
      setIsSyncing(false)
    }
  }

  async function ingestFilingContent(filing: Filing) {
    setIngestingFilingId(filing.id)
    setFilingsError(null)
    setSyncMessage(null)
    try {
      const response = await fetch(`${API_URL}/api/filings/${filing.id}/ingest`, {
        method: 'POST',
      })
      if (!response.ok) throw new Error(await responseError(response))
      const result = (await response.json()) as FilingIngestResult
      setFilings((currentFilings) =>
        currentFilings.map((currentFiling) =>
          currentFiling.id === filing.id
            ? { ...currentFiling, ingested_at: result.ingested_at }
            : currentFiling,
        ),
      )
      setSyncMessage(
        `Extracted ${result.sections_created} sections and ${result.chunks_created} chunks from ${filing.form}.`,
      )
    } catch (requestError) {
      if (requestError instanceof Error) setFilingsError(requestError.message)
    } finally {
      setIngestingFilingId(null)
    }
  }

  async function ingestAllPendingFilings() {
    const pendingFilings = filings.filter((filing) => !filing.ingested_at)
    if (pendingFilings.length === 0) {
      setSyncMessage('All stored filings for this company are already ingested.')
      return
    }

    setFilingsError(null)
    setSyncMessage(null)
    setBulkProgress({ completed: 0, total: pendingFilings.length, failed: 0 })
    let failed = 0
    let firstError: string | null = null

    for (const [index, filing] of pendingFilings.entries()) {
      setIngestingFilingId(filing.id)
      try {
        const response = await fetch(`${API_URL}/api/filings/${filing.id}/ingest`, {
          method: 'POST',
        })
        if (!response.ok) throw new Error(await responseError(response))
        const result = (await response.json()) as FilingIngestResult
        setFilings((currentFilings) =>
          currentFilings.map((currentFiling) =>
            currentFiling.id === filing.id
              ? { ...currentFiling, ingested_at: result.ingested_at }
              : currentFiling,
          ),
        )
      } catch (requestError) {
        failed += 1
        if (!firstError && requestError instanceof Error) firstError = requestError.message
      }

      setBulkProgress({ completed: index + 1, total: pendingFilings.length, failed })
      if (index + 1 < pendingFilings.length) {
        await new Promise((resolve) => window.setTimeout(resolve, 250))
      }
    }

    setIngestingFilingId(null)
    setBulkProgress(null)
    const succeeded = pendingFilings.length - failed
    setSyncMessage(`Ingested ${succeeded} of ${pendingFilings.length} pending filings.`)
    if (failed > 0) {
      setFilingsError(`${failed} filings failed. First error: ${firstError ?? 'Unknown error'}`)
    }
  }

  async function reingestAllFilings() {
    if (!selectedCompany) return

    setIsReingestingAll(true)
    setFilingsError(null)
    setSyncMessage(null)
    try {
      const response = await fetch(`${API_URL}/api/filings/reingest-all`, {
        method: 'POST',
      })
      if (!response.ok) throw new Error(await responseError(response))
      const result = (await response.json()) as FilingsReingestResult

      const filingsResponse = await fetch(
        `${API_URL}/api/companies/${selectedCompany.ticker}/filings`,
      )
      if (!filingsResponse.ok) throw new Error(await responseError(filingsResponse))
      setFilings((await filingsResponse.json()) as Filing[])

      if (result.total === 0) {
        setSyncMessage('There are no stored filings to re-ingest.')
      } else {
        setSyncMessage(`Re-ingested ${result.succeeded} of ${result.total} stored filings.`)
      }
      if (result.failed > 0) {
        const firstFailure = result.failures[0]
        setFilingsError(
          `${result.failed} filings failed. First error: ${firstFailure?.detail ?? 'Unknown error'}`,
        )
      }
    } catch (requestError) {
      if (requestError instanceof Error) setFilingsError(requestError.message)
    } finally {
      setIsReingestingAll(false)
    }
  }

  async function generateEmbeddings() {
    if (!selectedCompany) return

    setIsEmbeddingSyncing(true)
    setEmbeddingProgress({ completed: 0, total: 0, remaining: 0, model: null })
    setFilingsError(null)
    setSyncMessage(null)
    let completed = 0

    try {
      let result: ChunkEmbeddingSyncResult
      do {
        const response = await fetch(
          `${API_URL}/api/companies/${selectedCompany.ticker}/embeddings/sync?limit=500`,
          { method: 'POST' },
        )
        if (!response.ok) throw new Error(await responseError(response))
        result = (await response.json()) as ChunkEmbeddingSyncResult
        completed += result.embedded
        const total = completed + result.remaining
        setEmbeddingProgress({
          completed,
          total,
          remaining: result.remaining,
          model: result.model,
        })

        if (result.remaining > 0 && result.embedded === 0) {
          throw new Error('Embedding generation made no progress. Please try again.')
        }
      } while (result.remaining > 0)

      setSyncMessage(
        completed > 0
          ? `Generated ${completed} embeddings. All ${result.total_chunks} ${selectedCompany.ticker} chunks are ready.`
          : result.total_chunks > 0
            ? `All ${result.total_chunks} ${selectedCompany.ticker} chunks are already embedded.`
            : `No ingested ${selectedCompany.ticker} chunks are available to embed.`,
      )
    } catch (requestError) {
      if (requestError instanceof Error) setFilingsError(requestError.message)
    } finally {
      setIsEmbeddingSyncing(false)
      setEmbeddingProgress(null)
    }
  }

  async function searchEvidence(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selectedCompany) return

    const query = searchQuery.trim()
    if (query.length < 2) {
      setSearchError('Enter at least two characters to search filing evidence.')
      return
    }

    setIsSearching(true)
    setSearchError(null)
    setHasSearched(true)
    try {
      const params = new URLSearchParams({ query, limit: '8' })
      if (searchForm) params.set('form', searchForm)
      if (searchAsOfDate) params.set('as_of_date', searchAsOfDate)

      const response = await fetch(
        `${API_URL}/api/companies/${selectedCompany.ticker}/search?${params.toString()}`,
      )
      if (!response.ok) throw new Error(await responseError(response))
      setSearchResults((await response.json()) as SemanticSearchResult[])
    } catch (requestError) {
      setSearchResults([])
      if (requestError instanceof Error) setSearchError(requestError.message)
    } finally {
      setIsSearching(false)
    }
  }

  return (
    <main>
      <header className="site-header">
        <a className="brand" href="/" aria-label="FinAgent Eval home">
          <span className="brand-mark">F</span>
          FinAgent Eval
        </a>
        <span className="status"><span /> Local prototype</span>
      </header>

      <section className="intro">
        <p className="eyebrow">Financial research reliability</p>
        <h1>SEC filings, ready for evaluation.</h1>
        <p className="lede">
          Choose a tracked company and sync its latest 10-K, 10-Q, and 8-K filing metadata directly
          from SEC EDGAR.
        </p>
      </section>

      <section className="company-section" aria-labelledby="company-heading">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Coverage universe</p>
            <h2 id="company-heading">Tracked companies</h2>
          </div>
          {!isCompaniesLoading && !companiesError && (
            <span className="count">{companies.length} companies</span>
          )}
        </div>

        {isCompaniesLoading && <p className="message">Loading companies…</p>}
        {companiesError && (
          <div className="error" role="alert">
            <strong>Couldn’t reach the companies API.</strong>
            <span>{companiesError}</span>
          </div>
        )}
        {!isCompaniesLoading && !companiesError && (
          <div className="company-grid">
            {companies.map((company) => (
              <button
                className={`company-card ${selectedCompany?.id === company.id ? 'selected' : ''}`}
                key={company.id}
                type="button"
                aria-pressed={selectedCompany?.id === company.id}
                onClick={() => setSelectedCompany(company)}
                disabled={
                  bulkProgress !== null ||
                  isSyncing ||
                  isReingestingAll ||
                  isEmbeddingSyncing ||
                  isSearching ||
                  ingestingFilingId !== null
                }
              >
                <span className="ticker">{company.ticker}</span>
                <span className="company-copy">
                  <strong>{company.name}</strong>
                  <small>SEC CIK {company.sec_cik}</small>
                </span>
                <span className="arrow" aria-hidden="true">→</span>
              </button>
            ))}
          </div>
        )}
      </section>

      {selectedCompany && (
        <section className="search-section" aria-labelledby="search-heading">
          <div className="search-heading">
            <div>
              <p className="eyebrow">Semantic retrieval</p>
              <h2 id="search-heading">Search {selectedCompany.ticker} evidence</h2>
              <p className="section-description">
                Find relevant passages across embedded SEC filings. Results are evidence, not an
                AI-generated answer.
              </p>
            </div>
            {hasSearched && !isSearching && !searchError && (
              <span className="count">{searchResults.length} results</span>
            )}
          </div>

          <form className="search-form" onSubmit={searchEvidence}>
            <label className="query-field">
              <span>Research question</span>
              <input
                type="search"
                value={searchQuery}
                onChange={(event) => setSearchQuery(event.target.value)}
                placeholder="What drove Data Center revenue growth?"
                minLength={2}
                maxLength={2000}
                required
              />
            </label>
            <label>
              <span>Filing type</span>
              <select value={searchForm} onChange={(event) => setSearchForm(event.target.value)}>
                <option value="">All forms</option>
                <option value="10-K">10-K</option>
                <option value="10-Q">10-Q</option>
                <option value="8-K">8-K</option>
              </select>
            </label>
            <label>
              <span>Filed on or before</span>
              <input
                type="date"
                value={searchAsOfDate}
                onChange={(event) => setSearchAsOfDate(event.target.value)}
              />
            </label>
            <button
              className="sync-button search-submit"
              type="submit"
              disabled={
                isSearching ||
                isEmbeddingSyncing ||
                isReingestingAll ||
                bulkProgress !== null ||
                ingestingFilingId !== null
              }
            >
              {isSearching ? 'Searching…' : 'Search evidence'}
            </button>
          </form>

          {searchError && <div className="error search-feedback" role="alert">{searchError}</div>}
          {isSearching && <p className="message search-feedback">Ranking filing passages…</p>}
          {hasSearched && !isSearching && !searchError && searchResults.length === 0 && (
            <div className="empty-state search-feedback">
              <strong>No matching evidence found.</strong>
              <span>Try a broader question or remove a filing filter.</span>
            </div>
          )}
          {!isSearching && searchResults.length > 0 && (
            <div className="evidence-results" aria-live="polite">
              {searchResults.map((result, index) => {
                const excerpt = result.content.slice(0, 700)
                const hasMore = result.content.length > excerpt.length
                return (
                  <article className="evidence-card" key={result.chunk_id}>
                    <div className="evidence-rank" aria-label={`Result ${index + 1}`}>
                      {String(index + 1).padStart(2, '0')}
                    </div>
                    <div className="evidence-body">
                      <div className="evidence-header">
                        <div>
                          <span className="form-badge">{result.form}</span>
                          <strong>{result.section_name}</strong>
                        </div>
                        <span className="similarity-score">
                          {Math.round(result.similarity * 100)}% match
                        </span>
                      </div>
                      <p className="evidence-content">
                        {excerpt}{hasMore ? '…' : ''}
                      </p>
                      {hasMore && (
                        <details className="evidence-details">
                          <summary>View full chunk</summary>
                          <p>{result.content}</p>
                        </details>
                      )}
                      <div className="evidence-footer">
                        <span>Filed {result.filing_date}</span>
                        <span>Report {result.report_date ?? '—'}</span>
                        <span>{result.token_count} tokens</span>
                        <span className="evidence-accession">{result.accession_number}</span>
                        <a href={result.source_url} target="_blank" rel="noreferrer">
                          Open SEC filing <span aria-hidden="true">↗</span>
                        </a>
                      </div>
                    </div>
                  </article>
                )
              })}
            </div>
          )}
        </section>
      )}

      {selectedCompany && (
        <section className="filings-section" aria-labelledby="filings-heading">
          <div className="filings-header">
            <div>
              <p className="eyebrow">Primary-source evidence</p>
              <h2 id="filings-heading">{selectedCompany.ticker} filings</h2>
              <p className="section-description">10-K, 10-Q, and 8-K filing metadata from EDGAR.</p>
            </div>
            <div className="header-actions">
              <button
                className="secondary-button"
                type="button"
                onClick={ingestAllPendingFilings}
                disabled={
                  bulkProgress !== null ||
                  isSyncing ||
                  isReingestingAll ||
                  isEmbeddingSyncing ||
                  isSearching ||
                  ingestingFilingId !== null ||
                  filings.length === 0
                }
              >
                {bulkProgress
                  ? `Ingesting ${bulkProgress.completed}/${bulkProgress.total}`
                  : 'Ingest all pending'}
              </button>
              <button
                className="secondary-button"
                type="button"
                onClick={reingestAllFilings}
                disabled={
                  isReingestingAll ||
                  bulkProgress !== null ||
                  isSyncing ||
                  isEmbeddingSyncing ||
                  isSearching ||
                  ingestingFilingId !== null
                }
                title="Regenerate sections and chunks for every stored filing"
              >
                {isReingestingAll ? 'Re-ingesting all…' : 'Re-ingest all'}
              </button>
              <button
                className="secondary-button"
                type="button"
                onClick={generateEmbeddings}
                disabled={
                  isEmbeddingSyncing ||
                  isSyncing ||
                  isReingestingAll ||
                  bulkProgress !== null ||
                  isSearching ||
                  ingestingFilingId !== null ||
                  isFilingsLoading ||
                  !filings.some((filing) => filing.ingested_at !== null)
                }
                title="Generate local semantic-search vectors for this company's chunks"
              >
                {isEmbeddingSyncing ? 'Generating embeddings…' : 'Generate embeddings'}
              </button>
              <button
                className="sync-button"
                type="button"
                onClick={syncFilings}
                disabled={
                  isSyncing ||
                  isReingestingAll ||
                  isEmbeddingSyncing ||
                  isSearching ||
                  bulkProgress !== null ||
                  ingestingFilingId !== null
                }
              >
                {isSyncing ? 'Syncing…' : 'Sync SEC filings'}
              </button>
            </div>
          </div>

          {bulkProgress && (
            <div className="bulk-progress" role="status">
              <div>
                <strong>Ingesting filing content</strong>
                <span>{bulkProgress.completed} of {bulkProgress.total} complete</span>
              </div>
              <progress value={bulkProgress.completed} max={bulkProgress.total} />
              {bulkProgress.failed > 0 && <small>{bulkProgress.failed} failed</small>}
            </div>
          )}
          {isReingestingAll && (
            <div className="bulk-progress" role="status">
              <div>
                <strong>Re-ingesting all stored filings</strong>
                <span>Regenerating sections and token-based chunks…</span>
              </div>
              <progress />
            </div>
          )}
          {isEmbeddingSyncing && embeddingProgress && (
            <div className="bulk-progress" role="status">
              <div>
                <strong>Generating local embeddings</strong>
                <span>
                  {embeddingProgress.total > 0
                    ? `${embeddingProgress.completed} of ${embeddingProgress.total} complete`
                    : 'Preparing the local model…'}
                </span>
              </div>
              {embeddingProgress.total > 0 ? (
                <progress value={embeddingProgress.completed} max={embeddingProgress.total} />
              ) : (
                <progress />
              )}
              {embeddingProgress.model && <small>{embeddingProgress.remaining} remaining</small>}
            </div>
          )}
          {syncMessage && <p className="success" role="status">{syncMessage}</p>}
          {filingsError && <div className="error" role="alert">{filingsError}</div>}
          {isFilingsLoading && <p className="message">Loading filings…</p>}
          {!isFilingsLoading && !filingsError && filings.length === 0 && (
            <div className="empty-state">
              <strong>No filings stored yet.</strong>
              <span>Use “Sync SEC filings” to import the latest filing metadata.</span>
            </div>
          )}
          {!isFilingsLoading && filings.length > 0 && (
            <div className="filings-table">
              <div className="filing-row filing-labels" aria-hidden="true">
                <span>Form</span><span>Filed</span><span>Report period</span><span>Accession</span><span>Actions</span>
              </div>
              {filings.map((filing) => (
                <div className="filing-row" key={filing.id}>
                  <strong className="form-badge">{filing.form}</strong>
                  <span>{filing.filing_date}</span>
                  <span>{filing.report_date ?? '—'}</span>
                  <span className="accession">{filing.accession_number}</span>
                  <span className="filing-actions">
                    <button
                      type="button"
                      onClick={() => ingestFilingContent(filing)}
                      disabled={
                        ingestingFilingId !== null ||
                        bulkProgress !== null ||
                        isSyncing ||
                        isReingestingAll ||
                        isEmbeddingSyncing ||
                        isSearching
                      }
                    >
                      {ingestingFilingId === filing.id
                        ? 'Ingesting…'
                        : filing.ingested_at
                          ? 'Re-ingest'
                          : 'Ingest content'}
                    </button>
                    <a href={filing.document_url} target="_blank" rel="noreferrer">
                      SEC <span aria-hidden="true">↗</span>
                    </a>
                  </span>
                </div>
              ))}
            </div>
          )}
        </section>
      )}
    </main>
  )
}

export default App
