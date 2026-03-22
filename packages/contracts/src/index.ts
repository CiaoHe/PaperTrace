import type { components, paths } from "./openapi";

export type HealthResponse = paths["/api/v1/health"]["get"]["responses"][200]["content"]["application/json"];

export type CreateAnalysisRequest = paths["/api/v1/analyses"]["post"]["requestBody"]["content"]["application/json"];

export type CreateAnalysisResponse = paths["/api/v1/analyses"]["post"]["responses"][202]["content"]["application/json"];

export type ExamplesResponse = paths["/api/v1/examples"]["get"]["responses"][200]["content"]["application/json"];

export type JobsResponse = paths["/api/v1/analyses"]["get"]["responses"][200]["content"]["application/json"];

export type ResultResponse =
  paths["/api/v1/analyses/{job_id}/result"]["get"]["responses"][200]["content"]["application/json"];
export type ReviewManifestReadyResponse =
  paths["/api/v1/analyses/{job_id}/review"]["get"]["responses"][200]["content"]["application/json"];
export type ReviewBuildPendingResponse =
  paths["/api/v1/analyses/{job_id}/review"]["get"]["responses"][202]["content"]["application/json"];
export type ReviewUnavailableApiResponse =
  paths["/api/v1/analyses/{job_id}/review"]["get"]["responses"][409]["content"]["application/json"];
export type ReviewFileResponse =
  paths["/api/v1/analyses/{job_id}/review/files/{file_id}"]["get"]["responses"][200]["content"]["application/json"];

export type JobStatus = components["schemas"]["JobStatus"];
export type JobStage = components["schemas"]["JobStage"];
export type JobStatusResponse = components["schemas"]["JobStatusResponse"];
export type BaseRepoCandidate = components["schemas"]["BaseRepoCandidate"];
export type PaperContribution = components["schemas"]["PaperContribution"];
export type DiffCodeAnchor = components["schemas"]["DiffCodeAnchor"];
export type DiffCluster = components["schemas"]["DiffCluster"];
export type ContributionMapping = components["schemas"]["ContributionMapping"];
export type AnalysisResult = components["schemas"]["AnalysisResult"];
export type GoldenCaseExample = components["schemas"]["GoldenCaseExample"];
export type ReviewBuildStatus = components["schemas"]["ReviewBuildStatus"];
export type ReviewBuildPhase = components["schemas"]["ReviewBuildPhase"];
export type ReviewBuildStatusResponse = components["schemas"]["ReviewBuildStatusResponse"];
export type ReviewUnavailableResponse = components["schemas"]["ReviewUnavailableResponse"];
export type ReviewDiffType = components["schemas"]["ReviewDiffType"];
export type ReviewMatchType = components["schemas"]["ReviewMatchType"];
export type ReviewSemanticStatus = components["schemas"]["ReviewSemanticStatus"];
export type ReviewFallbackMode = components["schemas"]["ReviewFallbackMode"];
export type ReviewContributionStatus = components["schemas"]["ReviewContributionStatus"];
export type ReviewBucketKind = components["schemas"]["ReviewBucketKind"];
export type ReviewStats = components["schemas"]["ReviewStats"];
export type ReviewFileEntry = components["schemas"]["ReviewFileEntry"];
export type ReviewBucket = components["schemas"]["ReviewBucket"];
export type ReviewClaimIndexEntry = components["schemas"]["ReviewClaimIndexEntry"];
export type ReviewContributionStatusEntry = components["schemas"]["ReviewContributionStatusEntry"];
export type ReviewSummaryCounts = components["schemas"]["ReviewSummaryCounts"];
export type ReviewFileTreeNode = components["schemas"]["ReviewFileTreeNode"];
export type ReviewManifest = components["schemas"]["ReviewManifest"];
export type ReviewHunk = components["schemas"]["ReviewHunk"];
export type ReviewFilePayload = components["schemas"]["ReviewFilePayload"];
