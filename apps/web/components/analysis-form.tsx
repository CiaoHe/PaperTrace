"use client";

import type { AnalysisResult, GoldenCaseExample, JobStatusResponse } from "@papertrace/contracts";
import { useEffect, useId, useState } from "react";

import { AnalysisResultsWorkbench } from "@/components/analysis-results-workbench";
import type { StructuredPaperSourceKind } from "@/lib/api";
import { createAnalysis, getAnalysis, getAnalysisResult, getExamples, getJobs } from "@/lib/api";

const DEFAULT_PAPER = "https://arxiv.org/abs/2106.09685 LoRA";
const DEFAULT_PAPER_SOURCE_KIND: StructuredPaperSourceKind = "arxiv";
const JOB_STAGE_ORDER = [
  "paper_fetch",
  "paper_parse",
  "repo_fetch",
  "ancestry_trace",
  "diff_analyze",
  "contribution_map",
  "persist_result",
] as const;

function statusClass(status: JobStatusResponse["status"]): string {
  return status === "failed" ? "status failed" : "status";
}

function jobProgressPercent(job: JobStatusResponse): number {
  if (job.status === "succeeded") {
    return 100;
  }
  if (!job.stage) {
    return 0;
  }

  const stageIndex = JOB_STAGE_ORDER.indexOf(job.stage);
  const normalizedIndex = stageIndex === -1 ? 0 : stageIndex;
  const stageProgress = job.stage_progress ?? 0;
  return Math.max(0, Math.min(100, Math.round(((normalizedIndex + stageProgress) / JOB_STAGE_ORDER.length) * 100)));
}

function jobStageLabel(job: JobStatusResponse): string {
  const parts = [job.stage ?? "pending", `${jobProgressPercent(job)}%`];
  if (job.stage_detail) {
    parts.push(job.stage_detail);
  } else if (job.summary) {
    parts.push(job.summary);
  }
  return parts.join(" · ");
}

function jobTimeline(job: JobStatusResponse) {
  return job.timeline ?? [];
}

export function AnalysisForm() {
  const paperSourceId = useId();
  const paperFileId = useId();
  const paperSourceKindId = useId();
  const [paperSource, setPaperSource] = useState(DEFAULT_PAPER);
  const [paperFile, setPaperFile] = useState<File | null>(null);
  const [paperSourceKind, setPaperSourceKind] = useState<StructuredPaperSourceKind>(DEFAULT_PAPER_SOURCE_KIND);
  const [forceReanalysis, setForceReanalysis] = useState(false);
  const [examples, setExamples] = useState<GoldenCaseExample[]>([]);
  const [jobs, setJobs] = useState<JobStatusResponse[]>([]);
  const [job, setJob] = useState<JobStatusResponse | null>(null);
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    void (async () => {
      try {
        const [nextExamples, nextJobs] = await Promise.all([getExamples(), getJobs()]);
        setExamples(nextExamples);
        setJobs(nextJobs.slice(0, 5));
      } catch {
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
            forceReanalysis,
          })
        : await createAnalysis({
            force_reanalysis: forceReanalysis,
            paper_input: {
              source_kind: paperSourceKind,
              source_ref: paperSource,
            },
          });
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
    setError(null);
  }

  return (
    <div className="grid grid-main">
      <section className="panel">
        <div className="panel-inner">
          <h2>Run a local analysis</h2>
          <p className="muted">
            Submit one paper link or one PDF. PaperTrace resolves the implementation repository, traces the upstream
            base, and maps code changes back to the paper claims.
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
            <label className="checkbox-field">
              <input
                checked={forceReanalysis}
                onChange={(event) => setForceReanalysis(event.target.checked)}
                type="checkbox"
              />
              <span>
                Force reanalysis
                <small className="muted">
                  Run the pipeline again instead of reusing an existing analysis for this paper.
                </small>
              </span>
            </label>
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
              <small>Stage: {jobStageLabel(job)}</small>
              {jobTimeline(job).length > 0 ? (
                <small>
                  Latest event: {jobTimeline(job)[jobTimeline(job).length - 1]?.detail ?? "No detail reported."}
                </small>
              ) : null}
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
            <h3>Recent local jobs</h3>
            {jobs.length > 0 ? (
              <div className="list">
                {jobs.map((listedJob) => (
                  <div className="item" key={listedJob.id}>
                    <h4>{listedJob.repo_url || "Repository pending inference"}</h4>
                    <p>
                      {listedJob.status} · {jobStageLabel(listedJob)}
                    </p>
                    <p>{listedJob.summary ?? listedJob.stage_detail ?? listedJob.paper_source}</p>
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

        {result ? (
          <div id={job ? `job-${job.id}` : undefined}>
            <AnalysisResultsWorkbench jobId={job?.id ?? null} result={result} submittedRepoUrl={job?.repo_url ?? ""} />
          </div>
        ) : null}
      </section>
    </div>
  );
}
