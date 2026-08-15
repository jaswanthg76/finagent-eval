import { useEffect, useState } from 'react'
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
  const [ingestingFilingId, setIngestingFilingId] = useState<number | null>(null)
  const [bulkProgress, setBulkProgress] = useState<BulkIngestProgress | null>(null)

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
                  ingestingFilingId !== null
                }
                title="Regenerate sections and chunks for every stored filing"
              >
                {isReingestingAll ? 'Re-ingesting all…' : 'Re-ingest all'}
              </button>
              <button
                className="sync-button"
                type="button"
                onClick={syncFilings}
                disabled={
                  isSyncing ||
                  isReingestingAll ||
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
                        isReingestingAll
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
