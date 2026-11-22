import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { api } from "../api/client"
import type {
  BenchmarkSweepRequest,
  DatasetGenerationConfig,
  ExperimentLaunchRequest,
} from "../api/types"

// Jobs are polled while in flight (pending/running), and stop polling once
// they land on a terminal status - this is the one place "live" behavior
// happens, and it only ever reflects what the backend actually reports.
const TERMINAL_STATUSES = new Set(["completed", "failed"])

export function usePipelines() {
  return useQuery({ queryKey: ["pipelines"], queryFn: api.pipelines })
}

export function useDatasets() {
  return useQuery({ queryKey: ["datasets"], queryFn: api.datasets })
}

export function useDataset(datasetId: string | undefined) {
  return useQuery({
    queryKey: ["dataset", datasetId],
    queryFn: () => api.dataset(datasetId!),
    enabled: !!datasetId,
  })
}

export function useCreateDataset() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (config: DatasetGenerationConfig) => api.createDataset(config),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["datasets"] }),
  })
}

export function useExperiments(filters?: { dataset_id?: string; pipeline_name?: string }) {
  return useQuery({
    queryKey: ["experiments", filters],
    queryFn: () => api.experiments(filters),
  })
}

export function useExperiment(experimentId: string | undefined) {
  return useQuery({
    queryKey: ["experiment", experimentId],
    queryFn: () => api.experiment(experimentId!),
    enabled: !!experimentId,
  })
}

export function useJob(jobId: string | undefined) {
  return useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api.job(jobId!),
    enabled: !!jobId,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status && TERMINAL_STATUSES.has(status) ? false : 500
    },
  })
}

export function useLaunchExperiment() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: ExperimentLaunchRequest) => api.launchExperiment(body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["experiments"] }),
  })
}

export function useLaunchBenchmark() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (body: BenchmarkSweepRequest) => api.launchBenchmark(body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["experiments"] }),
  })
}

export function useBenchmarkResult(jobId: string | undefined, jobStatus: string | undefined) {
  return useQuery({
    queryKey: ["benchmarkResult", jobId],
    queryFn: () => api.benchmarkResult(jobId!),
    enabled: !!jobId && jobStatus === "completed",
  })
}
