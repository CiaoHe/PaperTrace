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
  const firstMapping = result.mappings[0] ?? null;
  const firstContribution = result.contributions[0] ?? null;
  const firstCluster = result.diff_clusters[0] ?? null;
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
  const relatedMapping = result.mappings.find((mapping) => mapping.contribution_id === contributionId) ?? null;
  return {
    mappingKey: relatedMapping ? mappingKey(relatedMapping) : null,
    contributionId,
    clusterId: relatedMapping?.diff_cluster_id ?? null,
  };
}

export function focusCluster(result: AnalysisResult, clusterId: string): WorkbenchFocus {
  const relatedMapping = result.mappings.find((mapping) => mapping.diff_cluster_id === clusterId) ?? null;
  return {
    mappingKey: relatedMapping ? mappingKey(relatedMapping) : null,
    contributionId: relatedMapping?.contribution_id ?? null,
    clusterId,
  };
}
