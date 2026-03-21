import type {
  AnalysisResult,
  CreateAnalysisRequest,
  CreateAnalysisResponse,
  ExamplesResponse,
  GoldenCaseExample,
  JobStatusResponse,
  JobsResponse,
  ResultResponse,
} from "@papertrace/contracts";

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export type StructuredPaperSourceKind = "arxiv" | "pdf_url" | "text_reference";

export interface CreateAnalysisUploadPayload {
  paperFile: File;
  paperSource?: string;
  forceReanalysis?: boolean;
}

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || `Request failed with status ${response.status}`);
  }

  return (await response.json()) as T;
}

export async function createAnalysis(
  payload: CreateAnalysisRequest | CreateAnalysisUploadPayload,
): Promise<JobStatusResponse> {
  const response =
    "paperFile" in payload
      ? await fetch(`${API_BASE_URL}/api/v1/analyses`, {
          method: "POST",
          body: (() => {
            const formData = new FormData();
            formData.set("paper_file", payload.paperFile);
            formData.set(
              "paper_input",
              JSON.stringify({
                source_kind: "pdf_file",
                source_ref: payload.paperSource || payload.paperFile.name,
              }),
            );
            if (payload.forceReanalysis) {
              formData.set("force_reanalysis", "true");
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
