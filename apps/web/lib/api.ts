import type {
  AnalysisResult,
  CreateAnalysisRequest,
  CreateAnalysisResponse,
  ExamplesResponse,
  GoldenCaseExample,
  JobStatusResponse,
  JobsResponse,
  ResultResponse,
  ReviewBuildPendingResponse,
  ReviewBuildStatusResponse,
  ReviewFilePayload,
  ReviewFileResponse,
  ReviewManifest,
  ReviewManifestReadyResponse,
  ReviewUnavailableApiResponse,
  ReviewUnavailableResponse,
} from "@papertrace/contracts";

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

export type StructuredPaperSourceKind = "arxiv" | "pdf_url" | "text_reference";

export interface CreateAnalysisUploadPayload {
  paperFile: File;
  paperSource?: string;
  forceReanalysis?: boolean;
}

export type AnalysisReviewState =
  | { kind: "ready"; review: ReviewManifest }
  | { kind: "building"; status: ReviewBuildStatusResponse }
  | { kind: "unavailable"; status: ReviewUnavailableResponse };

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

export async function getAnalysisReview(jobId: string): Promise<AnalysisReviewState> {
  const response = await fetch(`${API_BASE_URL}/api/v1/analyses/${jobId}/review`, {
    cache: "no-store",
  });

  if (response.status === 200) {
    const body = await parseResponse<ReviewManifestReadyResponse>(response);
    return { kind: "ready", review: body.review };
  }
  if (response.status === 202) {
    const body = await parseResponse<ReviewBuildPendingResponse>(response);
    return { kind: "building", status: body };
  }
  if (response.status === 409) {
    const body = await parseResponse<ReviewUnavailableApiResponse>(response);
    return { kind: "unavailable", status: body };
  }

  const body = await response.text();
  throw new Error(body || `Review request failed with status ${response.status}`);
}

export async function rebuildAnalysisReview(jobId: string): Promise<ReviewBuildStatusResponse> {
  const response = await fetch(`${API_BASE_URL}/api/v1/analyses/${jobId}/review/rebuild`, {
    method: "POST",
    cache: "no-store",
  });
  if (response.status === 409) {
    const body = await parseResponse<ReviewUnavailableApiResponse>(response);
    throw new Error(body.detail || body.build_error);
  }
  return await parseResponse<ReviewBuildStatusResponse>(response);
}

export async function getAnalysisReviewFile(jobId: string, fileId: string): Promise<ReviewFilePayload> {
  const response = await fetch(`${API_BASE_URL}/api/v1/analyses/${jobId}/review/files/${fileId}`, {
    cache: "no-store",
  });
  const body = await parseResponse<ReviewFileResponse>(response);
  return body.file;
}

export function resolveApiUrl(path: string): string {
  if (path.startsWith("http://") || path.startsWith("https://")) {
    return path;
  }
  return `${API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
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
