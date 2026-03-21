"use client";

import type { AnalysisResult, BaseRepoCandidate } from "@papertrace/contracts";
import { useMemo, useState } from "react";

interface AnalysisLineageGraphProps {
  result: AnalysisResult;
  submittedRepoUrl: string;
}

type ExplorerMode = "paths" | "rings";

type StrategyRing = {
  key: string;
  label: string;
  description: string;
  hopLabel: string;
  strategies: string[];
};

const STRATEGY_RINGS: StrategyRing[] = [
  {
    key: "direct",
    label: "Direct signals",
    description: "Fork metadata, README declarations, metadata URLs, and first-commit fossils.",
    hopLabel: "Hop 1",
    strategies: ["github_fork", "readme_base_declaration", "readme_declaration", "metadata_url", "fossil_evidence"],
  },
  {
    key: "structural",
    label: "Structural matches",
    description: "Framework signatures, dependency archaeology, code references, fingerprints, and repo shape.",
    hopLabel: "Hop 2",
    strategies: [
      "framework_signature",
      "dependency_archaeology",
      "code_reference",
      "code_fingerprint",
      "shape_similarity",
    ],
  },
  {
    key: "search",
    label: "Search expansion",
    description: "Paper mentions and remote code search widen the ancestry search space.",
    hopLabel: "Hop 3",
    strategies: ["paper_mention", "github_code_search"],
  },
  {
    key: "reasoning",
    label: "Reasoning fallback",
    description: "LLM or fallback reasoning when stronger evidence rings do not fully resolve lineage.",
    hopLabel: "Hop 4",
    strategies: ["llm_reasoning", "fallback"],
  },
];

function repoSlug(url: string): string {
  return url.replace("https://github.com/", "");
}

function repoOwner(url: string): string {
  return repoSlug(url).split("/")[0] ?? "unknown";
}

function strategyRingForCandidate(candidate: BaseRepoCandidate): StrategyRing {
  const fallbackRing = STRATEGY_RINGS[STRATEGY_RINGS.length - 1];
  return STRATEGY_RINGS.find((ring) => ring.strategies.includes(candidate.strategy)) ?? fallbackRing;
}

function sortCandidates(candidates: BaseRepoCandidate[]): BaseRepoCandidate[] {
  return [...candidates].sort((left, right) => {
    const leftRing = STRATEGY_RINGS.findIndex((ring) => ring.strategies.includes(left.strategy));
    const rightRing = STRATEGY_RINGS.findIndex((ring) => ring.strategies.includes(right.strategy));
    return leftRing - rightRing || right.confidence - left.confidence || left.repo_url.localeCompare(right.repo_url);
  });
}

export function AnalysisLineageGraph({ result, submittedRepoUrl }: AnalysisLineageGraphProps) {
  const [mode, setMode] = useState<ExplorerMode>("paths");

  const selectedCandidate =
    result.base_repo_candidates.find((candidate) => candidate.repo_url === result.selected_base_repo.repo_url) ??
    result.selected_base_repo;

  const sortedCandidates = useMemo(() => sortCandidates(result.base_repo_candidates), [result.base_repo_candidates]);
  const ringGroups = useMemo(
    () =>
      STRATEGY_RINGS.map((ring) => ({
        ring,
        candidates: sortedCandidates.filter((candidate) => ring.strategies.includes(candidate.strategy)),
      })).filter((group) => group.candidates.length > 0),
    [sortedCandidates],
  );

  const primaryPath = useMemo(() => {
    const selectedRing = strategyRingForCandidate(selectedCandidate);
    const peerCandidates = sortedCandidates.filter(
      (candidate) =>
        candidate.repo_url !== selectedCandidate.repo_url &&
        strategyRingForCandidate(candidate).key === selectedRing.key,
    );
    return [selectedCandidate, ...peerCandidates.slice(0, 2)];
  }, [selectedCandidate, sortedCandidates]);

  const ownerClusters = useMemo(() => {
    const clusters = new Map<string, BaseRepoCandidate[]>();
    for (const candidate of sortedCandidates) {
      const owner = repoOwner(candidate.repo_url);
      const existing = clusters.get(owner) ?? [];
      existing.push(candidate);
      clusters.set(owner, existing);
    }
    return [...clusters.entries()]
      .map(([owner, candidates]) => ({
        owner,
        candidates,
      }))
      .sort((left, right) => right.candidates.length - left.candidates.length || left.owner.localeCompare(right.owner));
  }, [sortedCandidates]);

  return (
    <div className="workbench-card">
      <div className="section-head">
        <div>
          <h4>Lineage explorer</h4>
          <p className="muted">
            Inspect ancestry by evidence depth, then switch to ring mode to see how direct signals, structural matches,
            and reasoning layers contributed to the final upstream pick.
          </p>
        </div>
        <div className="actions">
          <button
            className={`button secondary${mode === "paths" ? " active" : ""}`}
            onClick={() => setMode("paths")}
            type="button"
          >
            Hypothesis paths
          </button>
          <button
            className={`button secondary${mode === "rings" ? " active" : ""}`}
            onClick={() => setMode("rings")}
            type="button"
          >
            Signal rings
          </button>
        </div>
      </div>

      {mode === "paths" ? (
        <div className="stack">
          <div className="lineage-graph" role="img" aria-label="Repository lineage explorer path view">
            <div className="lineage-node source">
              <small>Submitted repo</small>
              <strong>{repoSlug(submittedRepoUrl)}</strong>
              <p>{submittedRepoUrl}</p>
            </div>
            <div className="lineage-edge">
              <span />
              <small>{strategyRingForCandidate(selectedCandidate).hopLabel}</small>
            </div>
            <div className="lineage-node selected">
              <small>Selected upstream</small>
              <strong>{repoSlug(selectedCandidate.repo_url)}</strong>
              <p>
                confidence {selectedCandidate.confidence.toFixed(2)} · {selectedCandidate.strategy}
              </p>
            </div>
          </div>

          <div className="detail-grid">
            <div className="item">
              <h4>Primary path</h4>
              <p>{strategyRingForCandidate(selectedCandidate).description}</p>
              <div className="pill-row">
                {primaryPath.map((candidate) => (
                  <code className="pill" key={`${candidate.repo_url}-${candidate.strategy}`}>
                    {repoSlug(candidate.repo_url)}
                  </code>
                ))}
              </div>
            </div>
            <div className="item">
              <h4>Repository families</h4>
              <p>Grouped by GitHub owner to highlight possible multi-hop ancestry neighborhoods.</p>
              <div className="pill-row">
                {ownerClusters.slice(0, 4).map((cluster) => (
                  <span className="pill" key={cluster.owner}>
                    {cluster.owner} · {cluster.candidates.length}
                  </span>
                ))}
              </div>
            </div>
          </div>

          <div className="lineage-branches">
            {ringGroups.map(({ ring, candidates }) => (
              <div className="lineage-branch" key={ring.key}>
                <div className="lineage-branch-node">
                  <small>
                    {ring.hopLabel} · {ring.label}
                  </small>
                  <strong>
                    {candidates[0]?.repo_url === selectedCandidate.repo_url ? "Selected lane" : "Alternate lane"}
                  </strong>
                  <p>{ring.description}</p>
                </div>
                <div className="list">
                  {candidates.map((candidate) => (
                    <div className="item" key={`${candidate.repo_url}-${candidate.strategy}`}>
                      <strong>{repoSlug(candidate.repo_url)}</strong>
                      <p>
                        confidence {candidate.confidence.toFixed(2)} · {candidate.strategy}
                      </p>
                      <p className="muted">{candidate.evidence}</p>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="stack">
          <div className="detail-grid">
            {ringGroups.map(({ ring, candidates }) => (
              <div className="item" key={ring.key}>
                <h4>
                  {ring.hopLabel} · {ring.label}
                </h4>
                <p>{ring.description}</p>
                <div className="list">
                  {candidates.map((candidate) => (
                    <div className="item" key={`${candidate.repo_url}-${candidate.strategy}`}>
                      <strong>{repoSlug(candidate.repo_url)}</strong>
                      <p>
                        {candidate.strategy} · confidence {candidate.confidence.toFixed(2)}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>

          <div className="workbench-card" style={{ marginTop: 8 }}>
            <h4>Review sequence</h4>
            <p className="muted">
              Start from direct signals, then structural matches, then search expansion, and only then inspect reasoning
              fallbacks.
            </p>
            <div className="pill-row">
              {ringGroups.map(({ ring }) => (
                <span className="pill" key={ring.key}>
                  {ring.hopLabel} · {ring.label}
                </span>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
