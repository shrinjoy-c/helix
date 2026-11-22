import type {
  BenchmarkJobResponse,
  BenchmarkSweepRequest,
  DatasetGenerationConfig,
  DatasetSummary,
  ExperimentLaunchRequest,
  ExperimentRecord,
  JobRecord,
  PipelineInfo,
} from "./types"

// In dev, Vite proxies /api -> the FastAPI server (see vite.config.ts).
// Override with VITE_API_BASE_URL for other setups (e.g. a deployed backend).
const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "/api"

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
    this.name = "ApiError"
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  })
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      detail = body.detail ?? detail
    } catch {
      // response had no JSON body; fall back to statusText
    }
    throw new ApiError(response.status, detail)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  health: () => request<{ status: string }>("/health"),

  pipelines: () => request<PipelineInfo[]>("/pipelines"),

  datasets: () => request<DatasetSummary[]>("/datasets"),
  dataset: (datasetId: string) => request<DatasetSummary>(`/datasets/${encodeURIComponent(datasetId)}`),
  createDataset: (config: DatasetGenerationConfig) =>
    request<DatasetSummary>("/datasets", { method: "POST", body: JSON.stringify(config) }),

  experiments: (filters?: { dataset_id?: string; pipeline_name?: string }) => {
    const params = new URLSearchParams()
    if (filters?.dataset_id) params.set("dataset_id", filters.dataset_id)
    if (filters?.pipeline_name) params.set("pipeline_name", filters.pipeline_name)
    const qs = params.toString()
    return request<ExperimentRecord[]>(`/experiments${qs ? `?${qs}` : ""}`)
  },
  experiment: (experimentId: string) =>
    request<ExperimentRecord>(`/experiments/${encodeURIComponent(experimentId)}`),
  launchExperiment: (body: ExperimentLaunchRequest) =>
    request<JobRecord>("/experiments", { method: "POST", body: JSON.stringify(body) }),

  launchBenchmark: (body: BenchmarkSweepRequest) =>
    request<JobRecord>("/benchmarks", { method: "POST", body: JSON.stringify(body) }),
  benchmarkResult: (jobId: string) =>
    request<BenchmarkJobResponse>(`/benchmarks/${encodeURIComponent(jobId)}`),

  jobs: () => request<JobRecord[]>("/jobs"),
  job: (jobId: string) => request<JobRecord>(`/jobs/${encodeURIComponent(jobId)}`),
}
