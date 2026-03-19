import type { components, paths } from "./openapi";

export type HealthResponse =
  paths["/api/v1/health"]["get"]["responses"][200]["content"]["application/json"];

export type CreateAnalysisRequest =
  paths["/api/v1/analyses"]["post"]["requestBody"]["content"]["application/json"];

export type CreateAnalysisResponse =
  paths["/api/v1/analyses"]["post"]["responses"][202]["content"]["application/json"];

export type ExamplesResponse =
  paths["/api/v1/examples"]["get"]["responses"][200]["content"]["application/json"];

export type JobsResponse =
  paths["/api/v1/analyses"]["get"]["responses"][200]["content"]["application/json"];

export type ResultResponse =
  paths["/api/v1/analyses/{job_id}/result"]["get"]["responses"][200]["content"]["application/json"];

export type JobStatus = components["schemas"]["JobStatus"];
export type JobStage = components["schemas"]["JobStage"];
export type JobStatusResponse = components["schemas"]["JobStatusResponse"];
export type BaseRepoCandidate = components["schemas"]["BaseRepoCandidate"];
export type PaperContribution = components["schemas"]["PaperContribution"];
export type DiffCluster = components["schemas"]["DiffCluster"];
export type ContributionMapping = components["schemas"]["ContributionMapping"];
export type AnalysisResult = components["schemas"]["AnalysisResult"];
export type GoldenCaseExample = components["schemas"]["GoldenCaseExample"];
