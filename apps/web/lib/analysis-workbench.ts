import type { AnalysisResult, ContributionMapping, DiffCluster, PaperContribution } from "@papertrace/contracts";

export interface WorkbenchFocus {
  mappingKey: string | null;
  contributionId: string | null;
  clusterId: string | null;
}

export function formatEnumLabel(value: string): string {
  return value.replaceAll("_", " ");
}

export function mappingKey(mapping: ContributionMapping): string {
  return `${mapping.diff_cluster_id}:${mapping.contribution_id}`;
}

function countAnchors(cluster: DiffCluster | null): number {
  return cluster?.code_anchors?.length ?? 0;
}

function compareMappingPriority(result: AnalysisResult, left: ContributionMapping, right: ContributionMapping): number {
  const leftCluster = findCluster(result, left.diff_cluster_id);
  const rightCluster = findCluster(result, right.diff_cluster_id);
  const leftAnchors = countAnchors(leftCluster);
  const rightAnchors = countAnchors(rightCluster);
  if (leftAnchors !== rightAnchors) {
    return rightAnchors - leftAnchors;
  }
  if (left.implementation_coverage !== right.implementation_coverage) {
    return right.implementation_coverage - left.implementation_coverage;
  }
  return right.confidence - left.confidence;
}

function selectPreferredMapping(
  result: AnalysisResult,
  predicate: (mapping: ContributionMapping) => boolean,
): ContributionMapping | null {
  const matches = result.mappings.filter(predicate);
  if (matches.length === 0) {
    return null;
  }
  return [...matches].sort((left, right) => compareMappingPriority(result, left, right))[0] ?? null;
}

export function buildCoverageBuckets(result: AnalysisResult): Record<string, number> {
  const buckets: Record<string, number> = { FULL: 0, PARTIAL: 0, APPROXIMATED: 0, MISSING: 0 };
  for (const mapping of result.mappings) {
    const coverageType = mapping.coverage_type ?? "PARTIAL";
    buckets[coverageType] = (buckets[coverageType] ?? 0) + 1;
  }
  return buckets;
}

export function readOptionalStringArray(
  value: AnalysisResult,
  key: "unmatched_contribution_ids" | "unmatched_diff_cluster_ids",
): string[] {
  if (!(key in value)) {
    return [];
  }
  const nextValue = value[key];
  return Array.isArray(nextValue) ? nextValue : [];
}

export function defaultFocus(result: AnalysisResult): WorkbenchFocus {
  const firstMapping = selectPreferredMapping(result, () => true);
  const firstContribution = result.contributions[0] ?? null;
  const firstCluster =
    (firstMapping ? findCluster(result, firstMapping.diff_cluster_id) : null) ?? result.diff_clusters[0] ?? null;
  return {
    mappingKey: firstMapping ? mappingKey(firstMapping) : null,
    contributionId: firstMapping?.contribution_id ?? firstContribution?.id ?? null,
    clusterId: firstMapping?.diff_cluster_id ?? firstCluster?.id ?? null,
  };
}

export function findContribution(result: AnalysisResult, contributionId: string | null): PaperContribution | null {
  if (!contributionId) {
    return null;
  }
  return result.contributions.find((contribution) => contribution.id === contributionId) ?? null;
}

export function findCluster(result: AnalysisResult, clusterId: string | null): DiffCluster | null {
  if (!clusterId) {
    return null;
  }
  return result.diff_clusters.find((cluster) => cluster.id === clusterId) ?? null;
}

export function findMapping(result: AnalysisResult, selectedMappingKey: string | null): ContributionMapping | null {
  if (!selectedMappingKey) {
    return null;
  }
  return result.mappings.find((mapping) => mappingKey(mapping) === selectedMappingKey) ?? null;
}

export function focusContribution(result: AnalysisResult, contributionId: string): WorkbenchFocus {
  const relatedMapping = selectPreferredMapping(result, (mapping) => mapping.contribution_id === contributionId);
  return {
    mappingKey: relatedMapping ? mappingKey(relatedMapping) : null,
    contributionId,
    clusterId: relatedMapping?.diff_cluster_id ?? null,
  };
}

export function focusCluster(result: AnalysisResult, clusterId: string): WorkbenchFocus {
  const relatedMapping = selectPreferredMapping(result, (mapping) => mapping.diff_cluster_id === clusterId);
  return {
    mappingKey: relatedMapping ? mappingKey(relatedMapping) : null,
    contributionId: relatedMapping?.contribution_id ?? null,
    clusterId,
  };
}
