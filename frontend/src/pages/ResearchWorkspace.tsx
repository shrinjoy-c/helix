import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { ApiError } from "../api/client"
import type { DatasetGenerationConfig } from "../api/types"
import { Card, StatTile } from "../components/Card"
import { EmptyState, ErrorState, LoadingState } from "../components/States"
import { StatusBadge } from "../components/StatusBadge"
import {
  useCreateDataset,
  useDatasets,
  useLaunchExperiment,
  usePipelines,
} from "../hooks/useApi"
import { TopBar } from "../layout/TopBar"

const DEFAULT_OBJECTIVE =
  "Determine whether the reference target is present in this sample and, if so, estimate " +
  "its abundance, while minimizing false positive detections."

function buildDatasetConfig(form: {
  datasetId: string
  description: string
  seed: number
  sequenceLength: number
  readDepth: number
  abundance: number
  present: boolean
  nBackground: number
  substitutionRate: number
  insertionRate: number
  deletionRate: number
  duplicateRate: number
}): DatasetGenerationConfig {
  return {
    dataset_id: form.datasetId,
    seed: form.seed,
    description: form.description,
    sequence_length: form.sequenceLength,
    read_depth: form.readDepth,
    targets: [{ target_id: "target_a", abundance: form.abundance, present: form.present }],
    n_background_sequences: form.nBackground,
    background_diversity: "medium",
    error_model: {
      substitution_rate: form.substitutionRate,
      insertion_rate: form.insertionRate,
      deletion_rate: form.deletionRate,
    },
    duplicate_rate: form.duplicateRate,
    barcode_length: 10,
    near_match_background_count: 0,
    near_match_edit_distance: 1,
  }
}

function DatasetCreationForm() {
  const createDataset = useCreateDataset()
  const [form, setForm] = useState({
    datasetId: `dataset_${Date.now().toString(36)}`,
    description: "",
    seed: 42,
    sequenceLength: 45,
    readDepth: 2000,
    abundance: 0.1,
    present: true,
    nBackground: 50,
    substitutionRate: 0.01,
    insertionRate: 0.0,
    deletionRate: 0.0,
    duplicateRate: 0.1,
  })

  function update<K extends keyof typeof form>(key: K, value: (typeof form)[K]) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    createDataset.mutate(buildDatasetConfig(form), {
      onSuccess: () => update("datasetId", `dataset_${Date.now().toString(36)}`),
    })
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <Field label="Dataset ID">
          <input
            className="input"
            value={form.datasetId}
            onChange={(e) => update("datasetId", e.target.value)}
            required
          />
        </Field>
        <Field label="Random seed (reproducibility)">
          <input
            type="number"
            className="input"
            value={form.seed}
            onChange={(e) => update("seed", Number(e.target.value))}
          />
        </Field>
      </div>
      <Field label="Description">
        <input
          className="input"
          placeholder="e.g. rare target with moderate indel rate"
          value={form.description}
          onChange={(e) => update("description", e.target.value)}
        />
      </Field>
      <div className="grid grid-cols-3 gap-3">
        <Field label="Sequence length">
          <input type="number" className="input" value={form.sequenceLength}
            onChange={(e) => update("sequenceLength", Number(e.target.value))} />
        </Field>
        <Field label="Read depth">
          <input type="number" className="input" value={form.readDepth}
            onChange={(e) => update("readDepth", Number(e.target.value))} />
        </Field>
        <Field label="Background sequences">
          <input type="number" className="input" value={form.nBackground}
            onChange={(e) => update("nBackground", Number(e.target.value))} />
        </Field>
      </div>
      <div className="rounded-lg border border-slate-800 bg-slate-950/40 p-3">
        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
          Target (ground truth - never exposed to the analysis step)
        </p>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Abundance (0-1)">
            <input type="number" step="0.01" min={0} max={1} className="input" value={form.abundance}
              onChange={(e) => update("abundance", Number(e.target.value))} />
          </Field>
          <Field label="Present in sample?">
            <select
              className="input"
              value={form.present ? "yes" : "no"}
              onChange={(e) => update("present", e.target.value === "yes")}
            >
              <option value="yes">Yes</option>
              <option value="no">No (true-negative control)</option>
            </select>
          </Field>
        </div>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <Field label="Substitution rate">
          <input type="number" step="0.001" className="input" value={form.substitutionRate}
            onChange={(e) => update("substitutionRate", Number(e.target.value))} />
        </Field>
        <Field label="Insertion rate">
          <input type="number" step="0.001" className="input" value={form.insertionRate}
            onChange={(e) => update("insertionRate", Number(e.target.value))} />
        </Field>
        <Field label="Deletion rate">
          <input type="number" step="0.001" className="input" value={form.deletionRate}
            onChange={(e) => update("deletionRate", Number(e.target.value))} />
        </Field>
      </div>
      <Field label="Duplicate rate">
        <input type="number" step="0.01" min={0} max={0.99} className="input" value={form.duplicateRate}
          onChange={(e) => update("duplicateRate", Number(e.target.value))} />
      </Field>

      <button type="submit" disabled={createDataset.isPending} className="btn-primary w-full">
        {createDataset.isPending ? "Generating..." : "Generate dataset"}
      </button>
      {createDataset.isError && (
        <ErrorState message={createDataset.error instanceof ApiError ? createDataset.error.message : "Failed to generate dataset"} />
      )}
      {createDataset.isSuccess && (
        <p className="text-sm text-emerald-400">
          Dataset {createDataset.data.dataset_id} generated.
        </p>
      )}
    </form>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-slate-400">{label}</span>
      {children}
    </label>
  )
}

function ExperimentLaunchForm() {
  const { data: datasets } = useDatasets()
  const { data: pipelines } = usePipelines()
  const launchExperiment = useLaunchExperiment()
  const navigate = useNavigate()

  const [datasetId, setDatasetId] = useState("")
  const [pipelineName, setPipelineName] = useState("")
  const [objective, setObjective] = useState(DEFAULT_OBJECTIVE)
  const [maxIterations, setMaxIterations] = useState(3)
  const [timeoutSeconds, setTimeoutSeconds] = useState(60)

  const selectedPipeline = pipelines?.find((p) => p.name === pipelineName)

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    if (!datasetId || !pipelineName) return
    launchExperiment.mutate(
      {
        dataset_id: datasetId, pipeline_name: pipelineName, research_objective: objective,
        max_iterations: maxIterations, timeout_seconds: timeoutSeconds,
      },
      { onSuccess: (job) => navigate(`/observatory/${job.job_id}`) },
    )
  }

  if (!datasets || datasets.length === 0) {
    return <EmptyState title="No datasets yet" hint="Generate one above before launching an experiment." />
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <Field label="Dataset">
        <select className="input" value={datasetId} onChange={(e) => setDatasetId(e.target.value)} required>
          <option value="" disabled>Select a dataset...</option>
          {datasets.map((d) => (
            <option key={d.dataset_id} value={d.dataset_id}>{d.dataset_id}</option>
          ))}
        </select>
      </Field>
      <Field label="Pipeline configuration">
        <select className="input" value={pipelineName} onChange={(e) => setPipelineName(e.target.value)} required>
          <option value="" disabled>Select a configuration...</option>
          {pipelines?.map((p) => (
            <option key={p.name} value={p.name}>{p.name}</option>
          ))}
        </select>
      </Field>
      {selectedPipeline && (
        <p className="text-xs text-slate-500">
          designer={String(selectedPipeline.enable_experiment_designer)} · reviewer=
          {String(selectedPipeline.enable_reviewer)} · verification loop=
          {String(selectedPipeline.enable_verification_loop)}
        </p>
      )}
      <Field label="Research objective">
        <textarea className="input h-20 resize-none" value={objective} onChange={(e) => setObjective(e.target.value)} />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Max iterations">
          <input type="number" min={1} className="input" value={maxIterations}
            onChange={(e) => setMaxIterations(Number(e.target.value))} />
        </Field>
        <Field label="Timeout (seconds)">
          <input type="number" min={1} className="input" value={timeoutSeconds}
            onChange={(e) => setTimeoutSeconds(Number(e.target.value))} />
        </Field>
      </div>
      <button type="submit" disabled={launchExperiment.isPending} className="btn-primary w-full">
        {launchExperiment.isPending ? "Launching..." : "Launch experiment"}
      </button>
      {launchExperiment.isError && (
        <ErrorState message={launchExperiment.error instanceof ApiError ? launchExperiment.error.message : "Launch failed"} />
      )}
    </form>
  )
}

function DatasetList() {
  const { data: datasets, isLoading, error } = useDatasets()
  if (isLoading) return <LoadingState label="Loading datasets..." />
  if (error) return <ErrorState message={(error as Error).message} />
  if (!datasets || datasets.length === 0) {
    return <EmptyState title="No datasets generated yet" hint="Use the form to generate your first synthetic dataset." />
  }
  return (
    <div className="space-y-2">
      {datasets.map((d) => (
        <div key={d.dataset_id} className="rounded-lg border border-slate-800 bg-slate-950/40 px-4 py-3">
          <div className="flex items-center justify-between">
            <p className="font-mono text-sm text-slate-200">{d.dataset_id}</p>
            <StatusBadge status="completed" />
          </div>
          {d.description && <p className="mt-1 text-xs text-slate-500">{d.description}</p>}
          <div className="mt-2 grid grid-cols-4 gap-2 text-xs text-slate-500">
            <span>len={d.metadata.sequence_length}</span>
            <span>depth={d.metadata.read_depth}</span>
            <span>bg={d.metadata.n_background_sequences}</span>
            <span>sub={d.metadata.error_model.substitution_rate}</span>
          </div>
        </div>
      ))}
    </div>
  )
}

export function ResearchWorkspace() {
  const { data: datasets } = useDatasets()
  const { data: pipelines } = usePipelines()

  return (
    <div>
      <TopBar
        title="Research Workspace"
        subtitle="Define the biological question, generate a dataset, and launch an experiment."
      />
      <div className="grid grid-cols-3 gap-4 p-8">
        <StatTile label="Datasets generated" value={datasets?.length ?? "-"} />
        <StatTile label="Available configurations" value={pipelines?.length ?? "-"} />
        <StatTile label="Backend" value="FastAPI" sublabel="helix.api.app" />
      </div>
      <div className="grid grid-cols-2 gap-6 px-8 pb-8">
        <div className="space-y-6">
          <Card title="1. Generate a synthetic dataset">
            <DatasetCreationForm />
          </Card>
          <Card title="Existing datasets">
            <DatasetList />
          </Card>
        </div>
        <Card title="2. Launch an experiment">
          <ExperimentLaunchForm />
        </Card>
      </div>
    </div>
  )
}
