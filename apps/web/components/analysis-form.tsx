"use client";

import type { AnalysisResult, GoldenCaseExample, JobStatusResponse } from "@papertrace/contracts";
import { useEffect, useId, useState } from "react";

import { createAnalysis, getAnalysis, getAnalysisResult, getExamples, getJobs } from "@/lib/api";

const DEFAULT_PAPER = "https://arxiv.org/abs/2106.09685 LoRA";
const DEFAULT_REPO = "https://github.com/microsoft/LoRA";

function statusClass(status: JobStatusResponse["status"]): string {
  return status === "failed" ? "status failed" : "status";
}

function formatEnumLabel(value: string): string {
  return value.replaceAll("_", " ");
}

function readOptionalStringArray(
  value: AnalysisResult,
  key: "unmatched_contribution_ids" | "unmatched_diff_cluster_ids",
): string[] {
  if (!(key in value)) {
    return [];
  }
  const nextValue = value[key];
  return Array.isArray(nextValue) ? nextValue : [];
}

export function AnalysisForm() {
  const paperSourceId = useId();
  const repoUrlId = useId();
  const [paperSource, setPaperSource] = useState(DEFAULT_PAPER);
  const [repoUrl, setRepoUrl] = useState(DEFAULT_REPO);
  const [examples, setExamples] = useState<GoldenCaseExample[]>([]);
  const [jobs, setJobs] = useState<JobStatusResponse[]>([]);
  const [job, setJob] = useState<JobStatusResponse | null>(null);
  const [result, setResult] = useState<AnalysisResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const unmatchedContributionIds = result ? readOptionalStringArray(result, "unmatched_contribution_ids") : [];
  const unmatchedDiffClusterIds = result ? readOptionalStringArray(result, "unmatched_diff_cluster_ids") : [];

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
      const createdJob = await createAnalysis({
        paper_source: paperSource,
        repo_url: repoUrl,
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
              <label htmlFor={paperSourceId}>Paper source</label>
              <input
                id={paperSourceId}
                name="paper-source"
                value={paperSource}
                onChange={(event) => setPaperSource(event.target.value)}
              />
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

        {result ? (
          <div className="panel">
            <div className="panel-inner stack">
              <div>
                <h3>Analysis summary</h3>
                <p className="muted">{result.summary}</p>
              </div>

              <div className="kpi">
                <small>Selected base repo</small>
                <strong>{result.selected_base_repo.repo_url}</strong>
                <span className="muted">
                  {result.selected_base_repo.strategy} • confidence {result.selected_base_repo.confidence.toFixed(2)}
                </span>
              </div>

              <div>
                <h3>Runtime provenance</h3>
                <div className="list">
                  <div className="item">
                    <h4>Paper fetch mode</h4>
                    <p>{formatEnumLabel(result.metadata.paper_fetch_mode)}</p>
                  </div>
                  <div className="item">
                    <h4>Paper source kind</h4>
                    <p>{formatEnumLabel(result.metadata.paper_source_kind)}</p>
                  </div>
                  <div className="item">
                    <h4>Parser mode</h4>
                    <p>{formatEnumLabel(result.metadata.parser_mode)}</p>
                  </div>
                  <div className="item">
                    <h4>Repo tracer mode</h4>
                    <p>{formatEnumLabel(result.metadata.repo_tracer_mode)}</p>
                  </div>
                  <div className="item">
                    <h4>Diff analyzer mode</h4>
                    <p>{formatEnumLabel(result.metadata.diff_analyzer_mode)}</p>
                  </div>
                  <div className="item">
                    <h4>Contribution mapper mode</h4>
                    <p>{formatEnumLabel(result.metadata.contribution_mapper_mode)}</p>
                  </div>
                  <div className="item">
                    <h4>Selected repo strategy</h4>
                    <p>{formatEnumLabel(result.metadata.selected_repo_strategy)}</p>
                  </div>
                </div>
              </div>

              <div>
                <h3>Base repo candidates</h3>
                <div className="list">
                  {result.base_repo_candidates.map((candidate) => (
                    <div className="item" key={`${candidate.repo_url}-${candidate.strategy}`}>
                      <h4>{candidate.repo_url}</h4>
                      <p>
                        {candidate.strategy} · confidence {candidate.confidence.toFixed(2)}
                      </p>
                      <p>{candidate.evidence}</p>
                    </div>
                  ))}
                </div>
              </div>

              <div>
                <h3>Contributions</h3>
                <div className="list">
                  {result.contributions.map((contribution) => (
                    <div className="item" key={contribution.id}>
                      <h4>
                        {contribution.id} · {contribution.title}
                      </h4>
                      <p>{contribution.section}</p>
                      <p>{contribution.impl_hints.join(" ")}</p>
                    </div>
                  ))}
                </div>
              </div>

              <div>
                <h3>Diff clusters</h3>
                <div className="list">
                  {result.diff_clusters.map((cluster) => (
                    <div className="item" key={cluster.id}>
                      <h4>
                        {cluster.id} · {cluster.label}
                      </h4>
                      <p>
                        {cluster.change_type} · {cluster.summary}
                      </p>
                      <p>
                        {cluster.files.map((file) => (
                          <code key={file} style={{ display: "block" }}>
                            {file}
                          </code>
                        ))}
                      </p>
                    </div>
                  ))}
                </div>
              </div>

              <div>
                <h3>Contribution mappings</h3>
                {result.mappings.length > 0 ? (
                  <div className="list">
                    {result.mappings.map((mapping) => (
                      <div className="item" key={`${mapping.diff_cluster_id}-${mapping.contribution_id}`}>
                        <h4>
                          {mapping.diff_cluster_id} → {mapping.contribution_id}
                        </h4>
                        <p>
                          confidence {mapping.confidence.toFixed(2)} · {mapping.completeness}
                        </p>
                        <p>{mapping.evidence}</p>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="muted">No confident mappings yet for this run.</p>
                )}
              </div>

              {unmatchedContributionIds.length > 0 ? (
                <div>
                  <h3>Unmatched contributions</h3>
                  <div className="list">
                    {unmatchedContributionIds.map((contributionId: string) => (
                      <div className="warning" key={contributionId}>
                        {contributionId}
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              {unmatchedDiffClusterIds.length > 0 ? (
                <div>
                  <h3>Unmatched diff clusters</h3>
                  <div className="list">
                    {unmatchedDiffClusterIds.map((clusterId: string) => (
                      <div className="warning" key={clusterId}>
                        {clusterId}
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              {result.metadata.fallback_notes.length > 0 ? (
                <div>
                  <h3>Runtime fallback notes</h3>
                  <div className="list">
                    {result.metadata.fallback_notes.map((note) => (
                      <div className="warning" key={note}>
                        {note}
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              {result.warnings.length > 0 ? (
                <div>
                  <h3>Warnings</h3>
                  <div className="list">
                    {result.warnings.map((warning) => (
                      <div className="warning" key={warning}>
                        {warning}
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
            </div>
          </div>
        ) : null}
      </section>
    </div>
  );
}
