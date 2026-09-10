import { useEffect, useRef, useState, type FormEvent, type ChangeEvent } from "react";
import "./App.css";

type Severity = "high" | "medium" | "low";
type Confidence = "high" | "medium" | "low";

interface Citation {
    article_id: string;
    title: string;
    url: string;
    relevance_note: string | null;
}

interface Finding {
    title: string;
    theme: string;
    severity: Severity;
    observation_confidence: Confidence;
    judgment_confidence: Confidence;
    what_i_see: string;
    why_it_matters: string;
    suggested_fix: string;
    caveat: string | null;
    citation: Citation | null;
}

interface AnalysisPayload {
    what_im_looking_at: string;
    whats_working: string[];
    findings: Finding[];
}

interface ApiResponse {
    findings: AnalysisPayload;
    cached: boolean;
    cache_key: string;
    screenshot: string;
    analyzed_at: string;
    context: string;
}

interface CaptureFailedDetail {
    error: "capture_failed";
    reason: string;
    hint: string;
}

type AppError =
    | { kind: "generic"; message: string }
    | { kind: "capture_failed"; detail: CaptureFailedDetail };

const ACCEPTED_MIME = ["image/png", "image/jpeg", "image/webp", "image/gif"];
const MAX_UPLOAD_BYTES = 5 * 1024 * 1024;

export default function App() {
    const [url, setUrl] = useState("");
    const [context, setContext] = useState("");
    const [file, setFile] = useState<File | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<AppError | null>(null);
    const [result, setResult] = useState<ApiResponse | null>(null);
    const resultsRef = useRef<HTMLDivElement>(null);
    useEffect(() => { if (result) resultsRef.current?.focus(); }, [result]);

    async function submit(send: () => Promise<Response>) {
        setError(null);
        setLoading(true);
        try {
            const resp = await send();
            if (!resp.ok) {
                // Try JSON first (FastAPI returns {detail: ...}); fall back to text.
                let parsed: { detail?: unknown } | null = null;
                try {
                    parsed = await resp.json();
                } catch {
                    // body wasn't JSON
                }
                const detail = parsed?.detail;
                if (
                    detail &&
                    typeof detail === "object" &&
                    (detail as Record<string, unknown>).error ===
                        "capture_failed"
                ) {
                    setError({
                        kind: "capture_failed",
                        detail: detail as CaptureFailedDetail,
                    });
                } else {
                    const message =
                        typeof detail === "string"
                            ? detail
                            : JSON.stringify(detail ?? `HTTP ${resp.status}`);
                    setError({
                        kind: "generic",
                        message: `${resp.status}: ${message}`,
                    });
                }
                return;
            }
            const data: ApiResponse = await resp.json();
            setResult(data);
        } catch (err) {
            setError({
                kind: "generic",
                message: err instanceof Error ? err.message : String(err),
            });
        } finally {
            setLoading(false);
        }
    }

    function onAnalyzeUrl(e: FormEvent) {
        e.preventDefault();
        if (!url || loading) return;
        submit(() =>
            fetch("/api/analyze", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url, context, refresh: (e.nativeEvent as SubmitEvent).submitter?.getAttribute("value") === "refresh" }),
            }),
        );
    }

    function onAnalyzeImage(e: FormEvent) {
        e.preventDefault();
        if (!file || loading) return;
        const fd = new FormData();
        fd.append("file", file);
        fd.append("context", context);
        submit(() => fetch("/api/analyze-image", { method: "POST", body: fd }));
    }

    function onFileChange(e: ChangeEvent<HTMLInputElement>) {
        const f = e.target.files?.[0] ?? null;
        if (f && (!ACCEPTED_MIME.includes(f.type) || f.size > MAX_UPLOAD_BYTES || f.size === 0)) {
            setError({
                kind: "generic",
                message: f.size > MAX_UPLOAD_BYTES ? "Choose an image smaller than 5 MB." : f.size === 0 ? "This image is empty. Choose another image." : "Choose a PNG, JPG, WEBP or GIF image.",
            });
            setFile(null);
            e.target.value = "";
            return;
        }
        setError(null);
        setFile(f);
    }

    return (
        <main className="container">
            <header className="hero">
                <h1>UX Insight Generator</h1>
                <p className="subtitle">
                    Paste a URL or upload a screenshot. Get a structured UX
                    critique from Claude.
                </p>
            </header>

            <label htmlFor="review-context">Who is this for, and what should they accomplish? (optional)</label>
            <textarea id="review-context" value={context} onChange={e => setContext(e.target.value)} maxLength={1000} disabled={loading} rows={3} placeholder="For example: First-time shoppers completing a purchase on their phone." />
            <label htmlFor="page-url">Page URL</label>
            <form className="input-row" onSubmit={onAnalyzeUrl}>
                <input
                    id="page-url"
                    type="url"
                    placeholder="https://example.com"
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                    disabled={loading}
                />
                <button type="submit" disabled={loading || !url}>
                    Analyze URL
                </button>
                <button type="submit" value="refresh" disabled={loading || !url} title="Capture the page again and run a new analysis">
                    Analyze again
                </button>
            </form>
            <p className="status">Analyze again captures a fresh page and runs a new analysis.</p>

            {error?.kind === "capture_failed" && (
                <div className="status capture-failed" role="alert">
                    <strong>Couldn&apos;t capture this URL:</strong>{" "}
                    {error.detail.reason}
                    <p className="hint">{error.detail.hint} ↓</p>
                </div>
            )}
            {error?.kind === "generic" && (
                <p className="status error" role="alert">Error: {error.message}</p>
            )}

            <div className="divider">
                <span>or</span>
            </div>

            <form className="upload-row" onSubmit={onAnalyzeImage}>
                <label className="file-label">
                    <input
                        type="file"
                        aria-label="Screenshot image"
                        aria-describedby="upload-help"
                        accept={ACCEPTED_MIME.join(",")}
                        onChange={onFileChange}
                        disabled={loading}
                    />
                    <span className="file-button">Choose Image</span>
                    <span className="file-name">
                        {file
                            ? `${file.name} (${formatBytes(file.size)})`
                            : "No file selected"}
                    </span>
                </label>
                <button type="submit" disabled={loading || !file}>
                    Analyze image
                </button>
            </form>
            <p id="upload-help" className="status">PNG, JPG, WEBP or GIF, up to 5 MB. Images are sent to the analysis provider and cached with the report for 24 hours.</p>

            {loading && (
                <p className="status" role="status">
                    Running analysis. First-time runs take ~20-30s; cache hits
                    return instantly.
                </p>
            )}
            {result && (loading || error) && <p className="status">Your previous review is still shown below.</p>}
            <div ref={resultsRef} tabIndex={-1} aria-label="Review results">
                {result && <Results key={result.analyzed_at} data={result} />}
            </div>
        </main>
    );
}

function formatBytes(n: number): string {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / 1024 / 1024).toFixed(2)} MB`;
}

function Results({ data }: { data: ApiResponse }) {
    const { findings, cached } = data;
    const [exportStatus, setExportStatus] = useState("");
    const ranked = [...findings.findings].sort((a, b) => ["high", "medium", "low"].indexOf(a.severity) - ["high", "medium", "low"].indexOf(b.severity));
    async function copyReport() {
        try {
            await navigator.clipboard.writeText(reportMarkdown(data));
            setExportStatus("Report copied.");
        } catch {
            setExportStatus("Could not access the clipboard. Use Download Markdown instead.");
        }
    }
    function downloadReport() {
        const url = URL.createObjectURL(new Blob([reportMarkdown(data)], { type: "text/markdown;charset=utf-8" }));
        const link = document.createElement("a");
        link.href = url;
        link.download = "ux-review.md";
        link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
    return (
        <section className="results">
            <div className="report-actions">
                <button onClick={copyReport}>Copy report</button>
                <button onClick={downloadReport}>Download Markdown</button>
            </div>
            <p role="status">{exportStatus}</p>
            <p className="status">Review started <time dateTime={data.analyzed_at}>{new Date(data.analyzed_at).toLocaleString()}</time></p>
            {data.context && <p><strong>Review context:</strong> {data.context}</p>}
            <figure className="screenshot-preview">
                <img src={data.screenshot} alt="Screenshot used for this UX review" />
                <figcaption>Screenshot reviewed. If this shows the wrong page or a login screen, upload your own screenshot.</figcaption>
            </figure>
            {cached && (
                <div
                    className="cache-badge"
                    title="No Anthropic call was made for this analysis."
                >
                    Served from cache
                </div>
            )}

            {ranked[0] ? <div className="priority-summary"><h2>Start here</h2><strong>{ranked[0].title}</strong><p>{ranked[0].suggested_fix}</p></div> : <p>No clear UX issues were identified in this screenshot. This is not a full usability or accessibility audit.</p>}
            <h2>What I&apos;m looking at</h2>
            <p>{findings.what_im_looking_at}</p>

            <h2>What&apos;s working</h2>
            <ul className="working">
                {findings.whats_working.map((s, i) => (
                    <li key={i}>{s}</li>
                ))}
            </ul>

            <h2>Findings</h2>
            <p className="status">Observation confidence means how clearly the issue is visible. Judgment confidence means how certain the reviewer is that it causes a problem.</p>
            <div className="findings">
                {ranked.map((f, i) => (
                    <FindingCard key={i} f={f} />
                ))}
            </div>
        </section>
    );
}

function FindingCard({ f }: { f: Finding }) {
    return (
        <article className={`finding sev-${f.severity}`}>
            <header className="finding-header">
                <h3>{f.title}</h3>
                <div className="badges">
                    <span className={`badge sev-${f.severity}`}>
                        {f.severity}
                    </span>
                    <span className="badge theme">
                        {f.theme.replace(/_/g, " ")}
                    </span>
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
                    <strong>Source:</strong>{" "}
                    <a
                        href={f.citation.url}
                        target="_blank"
                        rel="noopener noreferrer"
                    >
                        {f.citation.title}
                    </a>
                    {f.citation.relevance_note &&
                        `: ${f.citation.relevance_note}`}
                </p>
            )}
        </article>
    );
}

function reportMarkdown(data: ApiResponse): string {
    return [
        "# UX review", `Review started: ${data.analyzed_at}`, data.context ? `Context: ${data.context}` : "",
        "## What is being reviewed", data.findings.what_im_looking_at,
        "## What works", ...data.findings.whats_working.map(s => `- ${s}`),
        "## Findings", ...data.findings.findings.map(f => [
            `### ${f.title}`, `Severity: ${f.severity} | Theme: ${f.theme.replaceAll("_", " ")}`,
            `Observation confidence: ${f.observation_confidence} | Judgment confidence: ${f.judgment_confidence}`,
            `Observation: ${f.what_i_see}`, `Impact: ${f.why_it_matters}`, `Suggested fix: ${f.suggested_fix}`,
            f.caveat ? `Caveat: ${f.caveat}` : "",
            f.citation ? `Source: [${f.citation.title}](${f.citation.url})${f.citation.relevance_note ? `: ${f.citation.relevance_note}` : ""}` : "",
        ].filter(Boolean).join("\n\n")),
    ].filter(Boolean).join("\n\n");
}
