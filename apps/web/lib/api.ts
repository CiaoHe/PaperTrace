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

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

async function parseResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || `Request failed with status ${response.status}`);
  }

  return (await response.json()) as T;
}

export async function createAnalysis(payload: CreateAnalysisRequest): Promise<JobStatusResponse> {
  const response = await fetch(`${API_BASE_URL}/api/v1/analyses`, {
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
