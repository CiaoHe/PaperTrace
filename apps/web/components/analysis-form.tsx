"use client";

import type { AnalysisResult, GoldenCaseExample, HealthResponse, JobStatusResponse } from "@papertrace/contracts";
import { useEffect, useId, useState } from "react";

import { AnalysisResultsWorkbench } from "@/components/analysis-results-workbench";
import type { StructuredPaperSourceKind } from "@/lib/api";
import {
  API_BASE_URL,
  createAnalysis,
  getAnalysis,
  getAnalysisResult,
  getExamples,
  getHealth,
  getJobs,
} from "@/lib/api";

const DEFAULT_PAPER = "https://arxiv.org/abs/2106.09685 LoRA";
const DEFAULT_REPO = "https://github.com/microsoft/LoRA";
const DEFAULT_PAPER_SOURCE_KIND: StructuredPaperSourceKind = "arxiv";

function statusClass(status: JobStatusResponse["status"]): string {
  return status === "failed" ? "status failed" : "status";
}

function formatEnumLabel(value: string): string {
  return value.replaceAll("_", " ");
}

export function AnalysisForm() {
  const paperSourceId = useId();
  const paperFileId = useId();
  const repoUrlId = useId();
  const paperSourceKindId = useId();
  const [paperSource, setPaperSource] = useState(DEFAULT_PAPER);
  const [paperFile, setPaperFile] = useState<File | null>(null);
  const [paperSourceKind, setPaperSourceKind] = useState<StructuredPaperSourceKind>(DEFAULT_PAPER_SOURCE_KIND);
  const [repoUrl, setRepoUrl] = useState(DEFAULT_REPO);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [examples, setExamples] = useState<GoldenCaseExample[]>([]);
  const [jobs, setJobs] = useState<JobStatusResponse[]>([]);
  const [job, setJob] = useState<JobStatusResponse | null>(null);
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const [nextHealth, nextExamples, nextJobs] = await Promise.all([getHealth(), getExamples(), getJobs()]);
        setHealth(nextHealth);
        setExamples(nextExamples);
        setJobs(nextJobs.slice(0, 5));
      } catch {
        setHealth(null);
        setExamples([]);
        setJobs([]);
      }
    })();
  }, []);

  useEffect(() => {
    if (!job || job.status === "succeeded" || job.status === "failed") {
      return;
    }

    const interval = window.setInterval(async () => {
      try {
        const nextJob = await getAnalysis(job.id);
        setJob(nextJob);
        setJobs((currentJobs) => {
          const remainingJobs = currentJobs.filter((existingJob) => existingJob.id !== nextJob.id);
          return [nextJob, ...remainingJobs].slice(0, 5);
        });

        if (nextJob.status === "succeeded") {
          const nextResult = await getAnalysisResult(nextJob.id);
          setResult(nextResult);
          window.clearInterval(interval);
        }

        if (nextJob.status === "failed") {
          setError(nextJob.error_message ?? "Analysis failed");
          window.clearInterval(interval);
        }
      } catch (pollError) {
        setError(pollError instanceof Error ? pollError.message : "Failed to poll job");
        window.clearInterval(interval);
      }
    }, 1200);

    return () => window.clearInterval(interval);
  }, [job]);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    setResult(null);

    try {
      const createdJob = paperFile
        ? await createAnalysis({
            paperFile,
            paperSource: paperSource || undefined,
            paperSourceKind: "pdf_file",
            repoUrl,
          })
        : await createAnalysis(
            paperSourceKind === "arxiv" && paperSource.trim().includes("arxiv.org/")
              ? {
                  paper_source: paperSource,
                  repo_url: repoUrl,
                }
              : {
                  repo_url: repoUrl,
                  paper_input: {
                    source_kind: paperSourceKind,
                    source_ref: paperSource,
                  },
                },
          );
      setJob(createdJob);
      setJobs((currentJobs) => [createdJob, ...currentJobs.filter((item) => item.id !== createdJob.id)].slice(0, 5));

      if (createdJob.status === "succeeded") {
        const nextResult = await getAnalysisResult(createdJob.id);
        setResult(nextResult);
      }
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : "Failed to create analysis");
      setJob(null);
    } finally {
      setSubmitting(false);
    }
  }

  async function openJob(listedJob: JobStatusResponse) {
    setError(null);
    setPaperSource(listedJob.paper_source);
    setPaperFile(null);
    setRepoUrl(listedJob.repo_url);
    setJob(listedJob);

    if (listedJob.status === "failed") {
      setResult(null);
      setError(listedJob.error_message ?? "Analysis failed");
      return;
    }

    if (listedJob.result_available || listedJob.status === "succeeded") {
      try {
        const nextResult = await getAnalysisResult(listedJob.id);
        setResult(nextResult);
      } catch (loadError) {
        setResult(null);
        setError(loadError instanceof Error ? loadError.message : "Failed to load analysis result");
      }
      return;
    }

    setResult(null);
  }

  function applyExample(example: GoldenCaseExample) {
    setPaperSource(example.paper_source);
    setPaperFile(null);
    setPaperSourceKind(example.paper_source.includes(".pdf") ? "pdf_url" : "arxiv");
    setRepoUrl(example.repo_url);
    setError(null);
  }

  return (
    <div className="grid grid-main">
      <section className="panel">
        <div className="panel-inner">
          <h2>Run a local analysis</h2>
          <p className="muted">
            Submit an arXiv or PDF reference plus a GitHub repository URL. The local MVP prefers live public fetch and
            shallow-clone analysis, then falls back to fixtures when a stage cannot complete.
          </p>
          <form onSubmit={onSubmit}>
            {examples.length > 0 ? (
              <div className="field">
                <span className="muted" style={{ fontWeight: 700 }}>
                  Golden cases
                </span>
                <div className="chip-row">
                  {examples.map((example) => (
                    <button className="chip" key={example.slug} onClick={() => applyExample(example)} type="button">
                      {example.title}
                    </button>
                  ))}
                </div>
              </div>
            ) : null}
            <div className="field">
              <label htmlFor={paperSourceKindId}>Paper source type</label>
              <select
                id={paperSourceKindId}
                name="paper-source-kind"
                onChange={(event) => setPaperSourceKind(event.target.value as StructuredPaperSourceKind)}
                value={paperSourceKind}
              >
                <option value="arxiv">arXiv</option>
                <option value="pdf_url">PDF URL</option>
                <option value="text_reference">Text reference</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor={paperSourceId}>Paper source</label>
              <input
                id={paperSourceId}
                name="paper-source"
                placeholder="arXiv URL, PDF URL, or text reference"
                value={paperSource}
                onChange={(event) => setPaperSource(event.target.value)}
              />
            </div>
            <div className="field">
              <label htmlFor={paperFileId}>PDF upload</label>
              <input
                accept=".pdf,application/pdf"
                id={paperFileId}
                name="paper-file"
                onChange={(event) => setPaperFile(event.target.files?.[0] ?? null)}
                type="file"
              />
              <p className="muted">
                {paperFile ? `Selected file: ${paperFile.name}` : "Choose a local PDF to submit multipart/form-data."}
              </p>
            </div>
            <div className="field">
              <label htmlFor={repoUrlId}>Repository URL</label>
              <input
                id={repoUrlId}
                name="repo-url"
                value={repoUrl}
                onChange={(event) => setRepoUrl(event.target.value)}
              />
            </div>
            <div className="actions">
              <button className="button" disabled={submitting} type="submit">
                {submitting ? "Submitting..." : "Analyze"}
              </button>
              {job ? <span className={statusClass(job.status)}>{job.status}</span> : null}
            </div>
          </form>
          {job ? (
            <div className="kpi" style={{ marginTop: 18 }}>
              <strong>Job ID</strong>
              <code>{job.id}</code>
              <small>
                Stage: {job.stage ?? "pending"} {job.summary ? `• ${job.summary}` : ""}
              </small>
            </div>
          ) : null}
          {error ? (
            <div className="warning" style={{ marginTop: 18 }}>
              {error}
            </div>
          ) : null}
        </div>
      </section>

      <section className="stack">
        <div className="panel">
          <div className="panel-inner">
            <h3>What the MVP returns</h3>
            <div className="list">
              <div className="item">
                <h4>Base repo lineage</h4>
                <p>Ranked upstream candidates with strategy, confidence, and evidence.</p>
              </div>
              <div className="item">
                <h4>Semantic diff clusters</h4>
                <p>Filtered code changes grouped by technical purpose instead of raw file churn.</p>
              </div>
              <div className="item">
                <h4>Contribution mapping</h4>
                <p>Each diff cluster aligned to the paper contribution it appears to implement.</p>
              </div>
            </div>
          </div>
        </div>

        {health ? (
          <div className="panel">
            <div className="panel-inner">
              <h3>API runtime config</h3>
              <div className="list">
                <div className="item">
                  <h4>Execution defaults</h4>
                  <p>
                    live by default: {String(health.live_by_default)} · paper fetch: {String(health.live_paper_fetch)} ·
                    repo trace: {String(health.live_repo_trace)} · repo analysis: {String(health.live_repo_analysis)}
                  </p>
                </div>
                <div className="item">
                  <h4>Integrations</h4>
                  <p>
                    database: {health.database} · queue: {health.queue_mode} · llm configured:{" "}
                    {String(health.llm_configured)}
                  </p>
                </div>
                <div className="item">
                  <h4>Observed API endpoint</h4>
                  <p>{API_BASE_URL}</p>
                </div>
                <div className="item">
                  <h4>Paper source kinds</h4>
                  <p>{health.supported_paper_source_kinds.map(formatEnumLabel).join(" · ")}</p>
                </div>
              </div>
            </div>
          </div>
        ) : null}

        <div className="panel">
          <div className="panel-inner">
            <h3>Recent local jobs</h3>
            {jobs.length > 0 ? (
              <div className="list">
                {jobs.map((listedJob) => (
                  <div className="item" key={listedJob.id}>
                    <h4>{listedJob.repo_url}</h4>
                    <p>
                      {listedJob.status} {listedJob.stage ? `· ${listedJob.stage}` : ""}
                    </p>
                    <p>{listedJob.summary ?? listedJob.paper_source}</p>
                    <div className="actions" style={{ marginTop: 10 }}>
                      <button className="button secondary" onClick={() => openJob(listedJob)} type="button">
                        Open job
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <p className="muted">No local jobs yet. Run one analysis to seed the history.</p>
            )}
          </div>
        </div>

        {result ? <AnalysisResultsWorkbench result={result} /> : null}
      </section>
    </div>
  );
}
