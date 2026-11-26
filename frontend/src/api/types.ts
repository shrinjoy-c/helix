// Mirrors helix.api.schemas / helix.config.models / helix.benchmarking.metrics.
// Kept hand-in-sync with the backend rather than code-generated, since the
// backend is the single source of truth and these types only need to match
// its JSON shape closely enough for the UI to render safely.

export type PipelineKind = "deterministic" | "single_agent" | "multi_agent" | "parallel_multi_agent"

export interface PipelineInfo {
  name: string
  kind: PipelineKind
  enable_experiment_designer: boolean
  enable_reviewer: boolean
  enable_verification_loop: boolean
}

export interface ErrorModel {
  substitution_rate: number
  insertion_rate: number
  deletion_rate: number
}

export interface TargetSpec {
  target_id: string
  sequence?: string | null
  abundance: number
  present: boolean
  label?: string | null
}

export type BackgroundDiversity = "low" | "medium" | "high"

export interface DatasetGenerationConfig {
  dataset_id: string
  seed: number
  description: string
  sequence_length: number
  read_depth: number
  targets: TargetSpec[]
  n_background_sequences: number
  background_diversity: BackgroundDiversity
  error_model: ErrorModel
  duplicate_rate: number
  barcode_length: number
  near_match_background_count: number
  near_match_edit_distance: number
}

export interface DatasetMetadata {
  dataset_id: string
  description: string
  sequence_length: number
  read_depth: number
  n_background_sequences: number
  background_diversity: BackgroundDiversity
  error_model: ErrorModel
  duplicate_rate: number
  barcode_length: number
  reference_target_ids: string[]
}

export interface DatasetSummary {
  dataset_id: string
  description: string
  metadata: DatasetMetadata
}

export type JobStatus = "pending" | "running" | "completed" | "failed"
export type JobType = "experiment" | "benchmark_sweep"

export interface JobRecord {
  job_id: string
  job_type: JobType
  status: JobStatus
  created_at: string
  completed_at: string | null
  experiment_ids: string[]
  error: string | null
}

export interface PredictedTarget {
  target_id: string
  matched_read_count: number
  matched_molecule_count: number
  estimated_frequency: number
  detected: boolean
}

export interface AnalysisResult {
  dataset_id: string
  pipeline_name: string
  predictions: PredictedTarget[]
  total_reads_processed: number
  total_molecules_after_dedup: number
  runtime_seconds: number
  errors: string[]
}

export interface TargetEvaluation {
  target_id: string
  true_present: boolean
  predicted_present: boolean
  true_frequency: number
  estimated_frequency: number
  abundance_error: number
}

export interface EvaluationMetrics {
  dataset_id: string
  pipeline_name: string
  true_positives: number
  false_positives: number
  false_negatives: number
  true_negatives: number
  precision: number
  recall: number
  f1: number
  false_positive_rate: number
  false_negative_rate: number
  abundance_mae: number | null
  per_target: TargetEvaluation[]
}

export interface MatchingConfig {
  max_mismatches: number
  collapse_duplicates: boolean
  min_count: number
}

export type ExperimentStatus =
  | "pending" | "running" | "completed" | "needs_revision" | "failed" | "cancelled" | "timed_out"
  | "budget_exceeded"

export interface EventLogEntry {
  sequence: number
  event_type: string
  actor: string
  message: string
  payload: Record<string, unknown>
}

export interface ExperimentRecord {
  experiment_id: string
  dataset_id: string
  pipeline_name: string
  status: ExperimentStatus
  dataset_config: DatasetGenerationConfig
  matching_config: MatchingConfig | null
  analysis_result: AnalysisResult | null
  evaluation_metrics: EvaluationMetrics | null
  runtime_seconds: number
  created_at: string
  notes: string
  iteration_count: number | null
  token_usage: { input_tokens: number; output_tokens: number } | null
  estimated_cost_usd: number | null
  event_log: EventLogEntry[] | null
  repeat_index: number | null
  ablation: string | null
}

export interface MetricSummary {
  mean: number
  std: number
  n: number
}

export interface BenchmarkGroupSummary {
  dataset_id: string
  pipeline_name: string
  n_runs: number
  success_rate: number
  precision: MetricSummary
  recall: MetricSummary
  f1: MetricSummary
  false_positive_rate: MetricSummary
  false_negative_rate: MetricSummary
  abundance_mae: MetricSummary | null
  runtime_seconds: MetricSummary
  iteration_count: MetricSummary | null
  total_tokens: MetricSummary | null
  estimated_cost_usd: MetricSummary | null
}

export interface BenchmarkSweepResult {
  job_id: string
  experiment_ids: string[]
  summaries: BenchmarkGroupSummary[]
}

export interface BenchmarkJobResponse {
  job: JobRecord
  result: BenchmarkSweepResult | null
}

export interface AcceptanceCriteriaRequest {
  min_precision: number
  min_recall: number
  max_false_positive_rate: number
}

export type ProviderName = "mock" | "anthropic" | "ollama" | "openai_compatible"

export interface ProviderFields {
  provider?: ProviderName
  model?: string
  base_url?: string
  api_key_env?: string
}

export interface ExperimentLaunchRequest extends ProviderFields {
  dataset_id: string
  pipeline_name: string
  research_objective?: string
  max_iterations?: number
  timeout_seconds?: number
  max_branches?: number
  criteria?: AcceptanceCriteriaRequest
}

export interface BenchmarkSweepRequest extends ProviderFields {
  dataset_ids: string[]
  pipeline_names: string[]
  n_repeats?: number
  max_iterations?: number
  timeout_seconds?: number
  max_branches?: number
  criteria?: AcceptanceCriteriaRequest
}
