import type {
  AnalysisResult,
  CreateAnalysisRequest,
  CreateAnalysisResponse,
  ExamplesResponse,
  GoldenCaseExample,
  HealthResponse,
  JobStatusResponse,
  JobsResponse,
  ResultResponse,
} from "@papertrace/contracts";

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export type StructuredPaperSourceKind = "arxiv" | "pdf_url" | "text_reference";

export interface StructuredCreateAnalysisRequest {
  repo_url: string;
  paper_input: {
    source_kind: StructuredPaperSourceKind;
    source_ref: string;
  };
}

export interface CreateAnalysisUploadPayload {
  paperFile: File;
  paperSource?: string;
  paperSourceKind?: StructuredPaperSourceKind | "pdf_file";
  repoUrl: string;
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || `Request failed with status ${response.status}`);
  }

  return (await response.json()) as T;
}

export async function createAnalysis(
  payload: CreateAnalysisRequest | StructuredCreateAnalysisRequest | CreateAnalysisUploadPayload,
): Promise<JobStatusResponse> {
  const response =
    "paperFile" in payload
      ? await fetch(`${API_BASE_URL}/api/v1/analyses`, {
          method: "POST",
          body: (() => {
            const formData = new FormData();
            formData.set("repo_url", payload.repoUrl);
            formData.set("paper_file", payload.paperFile);
            if (payload.paperSource) {
              formData.set("paper_source", payload.paperSource);
            }
            if (payload.paperSourceKind) {
              formData.set("paper_source_kind", payload.paperSourceKind);
            }
            return formData;
          })(),
        })
      : await fetch(`${API_BASE_URL}/api/v1/analyses`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify(payload),
        });

  const body = await parseResponse<CreateAnalysisResponse>(response);
  return body.job;
}

export async function getAnalysis(jobId: string): Promise<JobStatusResponse> {
  const response = await fetch(`${API_BASE_URL}/api/v1/analyses/${jobId}`, {
    cache: "no-store",
  });
  const body = await parseResponse<CreateAnalysisResponse>(response);
  return body.job;
}

export async function getAnalysisResult(jobId: string): Promise<AnalysisResult> {
  const response = await fetch(`${API_BASE_URL}/api/v1/analyses/${jobId}/result`, {
    cache: "no-store",
  });
  const body = await parseResponse<ResultResponse>(response);
  return body.result;
}

export async function getExamples(): Promise<GoldenCaseExample[]> {
  const response = await fetch(`${API_BASE_URL}/api/v1/examples`, {
    cache: "no-store",
  });
  const body = await parseResponse<ExamplesResponse>(response);
  return body.examples;
}

export async function getJobs(): Promise<JobStatusResponse[]> {
  const response = await fetch(`${API_BASE_URL}/api/v1/analyses`, {
    cache: "no-store",
  });
  const body = await parseResponse<JobsResponse>(response);
  return body.jobs;
}

export async function getHealth(): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE_URL}/api/v1/health`, {
    cache: "no-store",
  });
  return parseResponse<HealthResponse>(response);
}
