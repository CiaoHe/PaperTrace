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

const LOCAL_API_DEFAULT_URL = "http://127.0.0.1:8000";

export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? LOCAL_API_DEFAULT_URL;

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

function normalizeBaseUrl(value: string): string {
  return value.endsWith("/") ? value.slice(0, -1) : value;
}

function isLoopbackHost(hostname: string): boolean {
  return hostname === "127.0.0.1" || hostname === "localhost" || hostname === "::1";
}

function resolveLocalFallbackApiBaseUrl(baseUrl: string): string | null {
  try {
    const parsed = new URL(baseUrl);
    const port = parsed.port || (parsed.protocol === "https:" ? "443" : "80");
    if (!isLoopbackHost(parsed.hostname) || port === "8000") {
      return null;
    }
    parsed.protocol = "http:";
    parsed.port = "8000";
    parsed.pathname = "";
    parsed.search = "";
    parsed.hash = "";
    return normalizeBaseUrl(parsed.toString());
  } catch {
    return null;
  }
}

async function describeNetworkFailure(requestUrl: string, error: unknown): Promise<Error> {
  const baseUrl = normalizeBaseUrl(API_BASE_URL);
  const fallbackBaseUrl = resolveLocalFallbackApiBaseUrl(baseUrl);
  if (fallbackBaseUrl) {
    try {
      const healthResponse = await fetch(`${fallbackBaseUrl}/api/v1/health`, {
        method: "HEAD",
        cache: "no-store",
      });
      if (healthResponse.ok) {
        return new Error(
          `Cannot reach configured API at ${requestUrl}. A local API is responding at ${fallbackBaseUrl}. Restart the web dev server so it picks up the current NEXT_PUBLIC_API_BASE_URL.`,
        );
      }
    } catch {
      // Ignore fallback probe failures and fall through to the generic message.
    }
  }

  const suffix = error instanceof Error && error.message ? ` ${error.message}` : "";
  return new Error(
    `Cannot reach API at ${requestUrl}. Check that the API server is running and restart the web dev server if NEXT_PUBLIC_API_BASE_URL changed.${suffix}`.trim(),
  );
}

async function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  const requestUrl = `${normalizeBaseUrl(API_BASE_URL)}${path.startsWith("/") ? path : `/${path}`}`;
  try {
    return await fetch(requestUrl, init);
  } catch (error) {
    throw await describeNetworkFailure(requestUrl, error);
  }
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
      ? await apiFetch("/api/v1/analyses", {
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
      : await apiFetch("/api/v1/analyses", {
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
  const response = await apiFetch(`/api/v1/analyses/${jobId}`, {
    cache: "no-store",
  });
  const body = await parseResponse<CreateAnalysisResponse>(response);
  return body.job;
}

export async function getAnalysisResult(jobId: string): Promise<AnalysisResult> {
  const response = await apiFetch(`/api/v1/analyses/${jobId}/result`, {
    cache: "no-store",
  });
  const body = await parseResponse<ResultResponse>(response);
  return body.result;
}

export async function getAnalysisReview(jobId: string): Promise<AnalysisReviewState> {
  const response = await apiFetch(`/api/v1/analyses/${jobId}/review`, {
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
  const response = await apiFetch(`/api/v1/analyses/${jobId}/review/rebuild`, {
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
  const response = await apiFetch(`/api/v1/analyses/${jobId}/review/files/${fileId}`, {
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
  const response = await apiFetch("/api/v1/examples", {
    cache: "no-store",
  });
  const body = await parseResponse<ExamplesResponse>(response);
  return body.examples;
}

export async function getJobs(): Promise<JobStatusResponse[]> {
  const response = await apiFetch("/api/v1/analyses", {
    cache: "no-store",
  });
  const body = await parseResponse<JobsResponse>(response);
  return body.jobs;
}
