import { useState } from "react"
import { useNavigate } from "react-router-dom"
import { ApiError } from "../api/client"
import type { ProviderFields } from "../api/types"
import { Card } from "../components/Card"
import { ProviderSelector } from "../components/ProviderSelector"
import { EmptyState, ErrorState } from "../components/States"
import { useDatasets, useLaunchBenchmark, usePipelines } from "../hooks/useApi"
import { TopBar } from "../layout/TopBar"

const BASELINE_NAMES = new Set([
  "deterministic_baseline", "single_agent", "sequential_multi_agent",
  "multi_agent_with_verification", "parallel_multi_agent",
  "dual_agent_no_verification", "dual_agent_with_verification",
  "adaptive_coordination",
  "experiment_a_single_no_verification", "experiment_b_single_with_verification",
  "experiment_c_dual_no_verification", "experiment_d_dual_with_verification", "experiment_e_adaptive",
])

function Checkbox({ checked, onChange, label, hint }: {
  checked: boolean; onChange: (v: boolean) => void; label: string; hint?: string
}) {
  return (
    <label className="flex items-start gap-2.5 rounded-lg border border-slate-800 bg-slate-950/40 px-3 py-2.5 hover:border-slate-700">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 h-4 w-4 rounded border-slate-600 bg-slate-900 accent-sky-600"
      />
      <span>
        <span className="block font-mono text-sm text-slate-200">{label}</span>
        {hint && <span className="block text-xs text-slate-500">{hint}</span>}
      </span>
    </label>
  )
}

export function ExperimentLab() {
  const { data: datasets } = useDatasets()
  const { data: pipelines } = usePipelines()
  const launchBenchmark = useLaunchBenchmark()
  const navigate = useNavigate()

  const [selectedDatasets, setSelectedDatasets] = useState<Set<string>>(new Set())
  const [selectedPipelines, setSelectedPipelines] = useState<Set<string>>(new Set())
  const [nRepeats, setNRepeats] = useState(2)
  const [maxIterations, setMaxIterations] = useState(3)
  const [timeoutSeconds, setTimeoutSeconds] = useState(30)
  const [maxBranches, setMaxBranches] = useState(3)
  const [maxTokens, setMaxTokens] = useState<number | "">("")
  const [maxCostUsd, setMaxCostUsd] = useState<number | "">("")
  const [providerFields, setProviderFields] = useState<ProviderFields>({ provider: "mock" })

  function toggle(set: Set<string>, setter: (s: Set<string>) => void, value: string) {
    const next = new Set(set)
    next.has(value) ? next.delete(value) : next.add(value)
    setter(next)
  }

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    if (selectedDatasets.size === 0 || selectedPipelines.size === 0) return
    launchBenchmark.mutate(
      {
        dataset_ids: [...selectedDatasets], pipeline_names: [...selectedPipelines],
        n_repeats: nRepeats, max_iterations: maxIterations, timeout_seconds: timeoutSeconds,
        max_branches: maxBranches,
        max_tokens: maxTokens === "" ? undefined : maxTokens,
        max_cost_usd: maxCostUsd === "" ? undefined : maxCostUsd,
        ...providerFields,
      },
      { onSuccess: (job) => navigate(`/results/${job.job_id}`) },
    )
  }

  const baselines = pipelines?.filter((p) => BASELINE_NAMES.has(p.name)) ?? []
  const ablations = pipelines?.filter((p) => !BASELINE_NAMES.has(p.name)) ?? []

  if (!datasets || datasets.length === 0) {
    return (
      <div>
        <TopBar title="Experiment Lab" subtitle="Configure baselines, agent combinations, ablations, and resource budgets." />
        <div className="p-8">
          <EmptyState title="No datasets available" hint="Generate at least one dataset in the Research Workspace first." />
        </div>
      </div>
    )
  }

  return (
    <div>
      <TopBar title="Experiment Lab" subtitle="Configure a controlled benchmark sweep across configurations." />
      <form onSubmit={handleSubmit} className="grid grid-cols-2 gap-6 p-8">
        <div className="space-y-6">
          <Card title="Datasets">
            <div className="space-y-2">
              {datasets.map((d) => (
                <Checkbox
                  key={d.dataset_id}
                  label={d.dataset_id}
                  hint={`len=${d.metadata.sequence_length} depth=${d.metadata.read_depth}`}
                  checked={selectedDatasets.has(d.dataset_id)}
                  onChange={() => toggle(selectedDatasets, setSelectedDatasets, d.dataset_id)}
                />
              ))}
            </div>
          </Card>

          <Card title="Baseline configurations">
            <div className="space-y-2">
              {baselines.map((p) => (
                <Checkbox
                  key={p.name}
                  label={p.name}
                  checked={selectedPipelines.has(p.name)}
                  onChange={() => toggle(selectedPipelines, setSelectedPipelines, p.name)}
                />
              ))}
            </div>
          </Card>

          <Card title="Ablations (F)">
            <div className="space-y-2">
              {ablations.map((p) => (
                <Checkbox
                  key={p.name}
                  label={p.name}
                  checked={selectedPipelines.has(p.name)}
                  onChange={() => toggle(selectedPipelines, setSelectedPipelines, p.name)}
                />
              ))}
            </div>
          </Card>
        </div>

        <div className="space-y-6">
          <Card title="Resource budget (applied identically to every configuration)">
            <div className="grid grid-cols-2 gap-3">
              <label className="block">
                <span className="mb-1 block text-xs font-medium text-slate-400">Repeats per configuration</span>
                <input type="number" min={1} className="input" value={nRepeats}
                  onChange={(e) => setNRepeats(Number(e.target.value))} />
              </label>
              <label className="block">
                <span className="mb-1 block text-xs font-medium text-slate-400">Max iterations</span>
                <input type="number" min={1} className="input" value={maxIterations}
                  onChange={(e) => setMaxIterations(Number(e.target.value))} />
              </label>
              <label className="block">
                <span className="mb-1 block text-xs font-medium text-slate-400">Timeout (seconds)</span>
                <input type="number" min={1} className="input" value={timeoutSeconds}
                  onChange={(e) => setTimeoutSeconds(Number(e.target.value))} />
              </label>
              <label className="block">
                <span className="mb-1 block text-xs font-medium text-slate-400">Max parallel branches</span>
                <input type="number" min={1} className="input" value={maxBranches}
                  onChange={(e) => setMaxBranches(Number(e.target.value))} />
              </label>
              <label className="block">
                <span className="mb-1 block text-xs font-medium text-slate-400">Max tokens (optional)</span>
                <input type="number" min={1} className="input" placeholder="unbounded" value={maxTokens}
                  onChange={(e) => setMaxTokens(e.target.value === "" ? "" : Number(e.target.value))} />
              </label>
              <label className="block">
                <span className="mb-1 block text-xs font-medium text-slate-400">Max cost USD (optional)</span>
                <input type="number" min={0} step="0.01" className="input" placeholder="unbounded" value={maxCostUsd}
                  onChange={(e) => setMaxCostUsd(e.target.value === "" ? "" : Number(e.target.value))} />
              </label>
            </div>
            <p className="mt-3 text-xs text-slate-500">
              Hard caps applied to every configuration in the sweep - a run that crosses either stops
              immediately with status "budget_exceeded". Leave blank for unbounded.
            </p>
          </Card>

          <ProviderSelector value={providerFields} onChange={setProviderFields} />

          <Card title="Summary">
            <p className="text-sm text-slate-400">
              {selectedDatasets.size} dataset(s) x {selectedPipelines.size} configuration(s) x {nRepeats}{" "}
              repeat(s) = <span className="font-mono text-slate-200">{selectedDatasets.size * selectedPipelines.size * nRepeats}</span> experiments
            </p>
            <button
              type="submit"
              disabled={launchBenchmark.isPending || selectedDatasets.size === 0 || selectedPipelines.size === 0}
              className="btn-primary mt-4 w-full"
            >
              {launchBenchmark.isPending ? "Launching sweep..." : "Launch benchmark sweep"}
            </button>
            {launchBenchmark.isError && (
              <div className="mt-3">
                <ErrorState message={launchBenchmark.error instanceof ApiError ? launchBenchmark.error.message : "Launch failed"} />
              </div>
            )}
          </Card>
        </div>
      </form>
    </div>
  )
}
