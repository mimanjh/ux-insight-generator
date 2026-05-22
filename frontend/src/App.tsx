import { useState, type FormEvent, type ChangeEvent } from 'react'
import './App.css'

type Severity = 'high' | 'medium' | 'low'
type Confidence = 'high' | 'medium' | 'low'

interface Citation {
  article_id: string
  title: string
  url: string
  relevance_note: string | null
}

interface Finding {
  title: string
  theme: string
  severity: Severity
  observation_confidence: Confidence
  judgment_confidence: Confidence
  what_i_see: string
  why_it_matters: string
  suggested_fix: string
  caveat: string | null
  citation: Citation | null
}

interface AnalysisPayload {
  what_im_looking_at: string
  whats_working: string[]
  findings: Finding[]
}

interface ApiResponse {
  findings: AnalysisPayload
  cached: boolean
  cache_key: string
}

interface CaptureFailedDetail {
  error: 'capture_failed'
  reason: string
  hint: string
}

type AppError = { kind: 'generic'; message: string } | { kind: 'capture_failed'; detail: CaptureFailedDetail }

const ACCEPTED_MIME = ['image/png', 'image/jpeg', 'image/webp', 'image/gif']

export default function App() {
  const [url, setUrl] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<AppError | null>(null)
  const [result, setResult] = useState<ApiResponse | null>(null)

  async function submit(send: () => Promise<Response>) {
    setError(null)
    setResult(null)
    setLoading(true)
    try {
      const resp = await send()
      if (!resp.ok) {
        // Try JSON first (FastAPI returns {detail: ...}); fall back to text.
        let parsed: { detail?: unknown } | null = null
        try {
          parsed = await resp.json()
        } catch {
          // body wasn't JSON
        }
        const detail = parsed?.detail
        if (
          detail &&
          typeof detail === 'object' &&
          (detail as Record<string, unknown>).error === 'capture_failed'
        ) {
          setError({ kind: 'capture_failed', detail: detail as CaptureFailedDetail })
        } else {
          const message =
            typeof detail === 'string' ? detail : JSON.stringify(detail ?? `HTTP ${resp.status}`)
          setError({ kind: 'generic', message: `${resp.status}: ${message}` })
        }
        return
      }
      const data: ApiResponse = await resp.json()
      setResult(data)
    } catch (err) {
      setError({
        kind: 'generic',
        message: err instanceof Error ? err.message : String(err),
      })
    } finally {
      setLoading(false)
    }
  }

  function onAnalyzeUrl(e: FormEvent) {
    e.preventDefault()
    if (!url || loading) return
    submit(() =>
      fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url }),
      }),
    )
  }

  function onAnalyzeImage(e: FormEvent) {
    e.preventDefault()
    if (!file || loading) return
    const fd = new FormData()
    fd.append('file', file)
    submit(() => fetch('/api/analyze-image', { method: 'POST', body: fd }))
  }

  function onFileChange(e: ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0] ?? null
    if (f && !ACCEPTED_MIME.includes(f.type)) {
      setError({
        kind: 'generic',
        message: `Unsupported file type: ${f.type || 'unknown'}`,
      })
      setFile(null)
      return
    }
    setError(null)
    setFile(f)
  }

  return (
    <main className="container">
      <header className="hero">
        <h1>UX Insight Generator</h1>
        <p className="subtitle">
          Paste a URL or upload a screenshot. Get a structured UX critique from Claude.
        </p>
      </header>

      <form className="input-row" onSubmit={onAnalyzeUrl}>
        <input
          type="url"
          placeholder="https://example.com"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          disabled={loading}
        />
        <button type="submit" disabled={loading || !url}>
          {loading ? 'Analyzing…' : 'Analyze URL'}
        </button>
      </form>

      {error?.kind === 'capture_failed' && (
        <div className="status capture-failed">
          <strong>Couldn&apos;t capture this URL:</strong> {error.detail.reason}
          <p className="hint">{error.detail.hint} ↓</p>
        </div>
      )}
      {error?.kind === 'generic' && (
        <p className="status error">Error: {error.message}</p>
      )}

      <div className="divider">
        <span>or</span>
      </div>

      <form className="upload-row" onSubmit={onAnalyzeImage}>
        <label className="file-label">
          <input
            type="file"
            accept={ACCEPTED_MIME.join(',')}
            onChange={onFileChange}
            disabled={loading}
          />
          <span className="file-button">Choose image</span>
          <span className="file-name">
            {file ? `${file.name} (${formatBytes(file.size)})` : 'No file selected'}
          </span>
        </label>
        <button type="submit" disabled={loading || !file}>
          {loading ? 'Analyzing…' : 'Analyze image'}
        </button>
      </form>

      {loading && (
        <p className="status">
          Running analysis. First-time runs take ~20-30s; cache hits return instantly.
        </p>
      )}
      {result && <Results data={result} />}
    </main>
  )
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(2)} MB`
}

function Results({ data }: { data: ApiResponse }) {
  const { findings, cached } = data
  return (
    <section className="results">
      {cached && (
        <div className="cache-badge" title="No Anthropic call was made for this analysis.">
          Served from cache
        </div>
      )}

      <h2>What I&apos;m looking at</h2>
      <p>{findings.what_im_looking_at}</p>

      <h2>What&apos;s working</h2>
      <ul className="working">
        {findings.whats_working.map((s, i) => (
          <li key={i}>{s}</li>
        ))}
      </ul>

      <h2>Findings</h2>
      <div className="findings">
        {findings.findings.map((f, i) => (
          <FindingCard key={i} f={f} />
        ))}
      </div>
    </section>
  )
}

function FindingCard({ f }: { f: Finding }) {
  return (
    <article className={`finding sev-${f.severity}`}>
      <header className="finding-header">
        <h3>{f.title}</h3>
        <div className="badges">
          <span className={`badge sev-${f.severity}`}>{f.severity}</span>
          <span className="badge theme">{f.theme.replace(/_/g, ' ')}</span>
        </div>
      </header>
      <div className="confidence">
        Observation <strong>{f.observation_confidence}</strong>
        <span className="sep">·</span>
        Judgment <strong>{f.judgment_confidence}</strong>
      </div>
      <dl className="finding-body">
        <dt>What I see</dt>
        <dd>{f.what_i_see}</dd>
        <dt>Why it matters</dt>
        <dd>{f.why_it_matters}</dd>
        <dt>Suggested fix</dt>
        <dd>{f.suggested_fix}</dd>
      </dl>
      {f.caveat && (
        <p className="caveat">
          <strong>Caveat:</strong> {f.caveat}
        </p>
      )}
      {f.citation && (
        <p className="citation">
          <strong>Source:</strong>{' '}
          <a href={f.citation.url} target="_blank" rel="noopener noreferrer">
            {f.citation.title}
          </a>
          {f.citation.relevance_note && ` — ${f.citation.relevance_note}`}
        </p>
      )}
    </article>
  )
}
