import { useMemo } from "react"
import {
  Bar,
  BarChart,
  CartesianGrid,
  ErrorBar,
  Legend,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"
import { useNavigate, useParams } from "react-router-dom"
import type { BenchmarkGroupSummary } from "../api/types"
import { CATEGORICAL, CHROME, colorForIndex } from "../charts/palette"
import { Card } from "../components/Card"
import { EmptyState, ErrorState, LoadingState } from "../components/States"
import { StatusBadge } from "../components/StatusBadge"
import { useBenchmarkResult, useJob } from "../hooks/useApi"
import { TopBar } from "../layout/TopBar"

function datasetIds(summaries: BenchmarkGroupSummary[]): string[] {
  return [...new Set(summaries.map((s) => s.dataset_id))].sort()
}

function pivotForBar(
  summaries: BenchmarkGroupSummary[],
  metric: "f1" | "runtime_seconds",
): Array<Record<string, number | string>> {
  const pipelines = [...new Set(summaries.map((s) => s.pipeline_name))].sort()
  const datasets = datasetIds(summaries)
  return pipelines.map((pipeline) => {
    const row: Record<string, number | string> = { pipeline }
    for (const dataset of datasets) {
      const match = summaries.find((s) => s.pipeline_name === pipeline && s.dataset_id === dataset)
      row[dataset] = match ? match[metric].mean : 0
      row[`${dataset}__std`] = match ? match[metric].std : 0
    }
    return row
  })
}

function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs shadow-xl">
      {label && <p className="mb-1 font-mono text-slate-300">{label}</p>}
      {payload
        .filter((p: any) => !String(p.dataKey).endsWith("__std"))
        .map((p: any) => (
          <p key={p.dataKey} style={{ color: p.color }}>
            {p.name}: {typeof p.value === "number" ? p.value.toFixed(3) : p.value}
          </p>
        ))}
    </div>
  )
}

function F1Chart({ summaries }: { summaries: BenchmarkGroupSummary[] }) {
  const data = useMemo(() => pivotForBar(summaries, "f1"), [summaries])
  const datasets = datasetIds(summaries)
  return (
    <ResponsiveContainer width="100%" height={300}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={CHROME.gridline} vertical={false} />
        <XAxis dataKey="pipeline" tick={{ fill: CHROME.mutedInk, fontSize: 11 }} interval={0} angle={-20} textAnchor="end" height={70} />
        <YAxis domain={[0, 1]} tick={{ fill: CHROME.mutedInk, fontSize: 11 }} />
        <Tooltip content={<ChartTooltip />} />
        {datasets.length > 1 && <Legend wrapperStyle={{ fontSize: 12, color: CHROME.secondaryInk }} />}
        {datasets.map((dataset, i) => (
          <Bar key={dataset} dataKey={dataset} name={dataset} fill={colorForIndex(i)} radius={[4, 4, 0, 0]}>
            <ErrorBar dataKey={`${dataset}__std`} stroke={CHROME.mutedInk} width={4} />
          </Bar>
        ))}
      </BarChart>
    </ResponsiveContainer>
  )
}

function RuntimeChart({ summaries }: { summaries: BenchmarkGroupSummary[] }) {
  const data = useMemo(() => pivotForBar(summaries, "runtime_seconds"), [summaries])
  const datasets = datasetIds(summaries)
  return (
    <ResponsiveContainer width="100%" height={280}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={CHROME.gridline} vertical={false} />
        <XAxis dataKey="pipeline" tick={{ fill: CHROME.mutedInk, fontSize: 11 }} interval={0} angle={-20} textAnchor="end" height={70} />
        <YAxis tick={{ fill: CHROME.mutedInk, fontSize: 11 }} unit="s" />
        <Tooltip content={<ChartTooltip />} />
        {datasets.length > 1 && <Legend wrapperStyle={{ fontSize: 12, color: CHROME.secondaryInk }} />}
        {datasets.map((dataset, i) => (
          <Bar key={dataset} dataKey={dataset} name={dataset} fill={colorForIndex(i + 2)} radius={[4, 4, 0, 0]}>
            <ErrorBar dataKey={`${dataset}__std`} stroke={CHROME.mutedInk} width={4} />
          </Bar>
        ))}
      </BarChart>
    </ResponsiveContainer>
  )
}

function PrecisionRecallChart({ summaries }: { summaries: BenchmarkGroupSummary[] }) {
  const datasets = datasetIds(summaries)
  return (
    <ResponsiveContainer width="100%" height={320}>
      <ScatterChart margin={{ top: 8, right: 16, left: 0, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={CHROME.gridline} />
        <XAxis type="number" dataKey="recall" domain={[0, 1.05]} name="Recall" tick={{ fill: CHROME.mutedInk, fontSize: 11 }} />
        <YAxis type="number" dataKey="precision" domain={[0, 1.05]} name="Precision" tick={{ fill: CHROME.mutedInk, fontSize: 11 }} />
        <Tooltip
          cursor={{ strokeDasharray: "3 3" }}
          content={({ active, payload }) => {
            if (!active || !payload?.length) return null
            const point = payload[0].payload
            return (
              <div className="rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs shadow-xl">
                <p className="font-mono text-slate-200">{point.pipeline_name}</p>
                <p className="text-slate-400">{point.dataset_id}</p>
                <p className="text-slate-400">recall={point.recall.toFixed(2)} precision={point.precision.toFixed(2)}</p>
              </div>
            )
          }}
        />
        {datasets.length > 1 && <Legend wrapperStyle={{ fontSize: 12, color: CHROME.secondaryInk }} />}
        {datasets.map((dataset, i) => (
          <Scatter
            key={dataset}
            name={dataset}
            // f1.n === 0 means this configuration never reached verification on any
            // repeat (e.g. every run was rejected by review) - there is no precision/
            // recall to plot, not a measured (0, 0). Omit rather than show a
            // misleading point at the origin.
            data={summaries
              .filter((s) => s.dataset_id === dataset && s.f1.n > 0)
              .map((s) => ({
                recall: s.recall.mean, precision: s.precision.mean,
                pipeline_name: s.pipeline_name, dataset_id: s.dataset_id,
              }))}
            fill={CATEGORICAL[i % CATEGORICAL.length]}
          />
        ))}
      </ScatterChart>
    </ResponsiveContainer>
  )
}

function SummaryTable({ summaries }: { summaries: BenchmarkGroupSummary[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-slate-800 text-xs uppercase tracking-wide text-slate-500">
            <th className="py-2 pr-4">Dataset</th>
            <th className="py-2 pr-4">Pipeline</th>
            <th className="py-2 pr-4">n</th>
            <th className="py-2 pr-4">Success</th>
            <th className="py-2 pr-4">F1</th>
            <th className="py-2 pr-4">Precision</th>
            <th className="py-2 pr-4">Recall</th>
            <th className="py-2 pr-4">Abundance MAE</th>
            <th className="py-2 pr-4">Runtime (s)</th>
            <th className="py-2 pr-4">Iterations</th>
            <th className="py-2 pr-4">Tokens</th>
            <th className="py-2 pr-4">Cost</th>
          </tr>
        </thead>
        <tbody className="font-mono text-xs text-slate-300">
          {summaries.map((s) => {
            const scored = s.f1.n > 0
            return (
              <tr key={`${s.dataset_id}-${s.pipeline_name}`} className="border-b border-slate-900">
                <td className="py-2 pr-4">{s.dataset_id}</td>
                <td className="py-2 pr-4">{s.pipeline_name}</td>
                <td className="py-2 pr-4">{s.n_runs}</td>
                <td className="py-2 pr-4">{(s.success_rate * 100).toFixed(0)}%</td>
                <td className="py-2 pr-4">{scored ? `${s.f1.mean.toFixed(2)}±${s.f1.std.toFixed(2)}` : "n/a"}</td>
                <td className="py-2 pr-4">{scored ? s.precision.mean.toFixed(2) : "n/a"}</td>
                <td className="py-2 pr-4">{scored ? s.recall.mean.toFixed(2) : "n/a"}</td>
                <td className="py-2 pr-4">{s.abundance_mae ? s.abundance_mae.mean.toFixed(4) : "n/a"}</td>
                <td className="py-2 pr-4">{s.runtime_seconds.mean.toFixed(3)}</td>
                <td className="py-2 pr-4">{s.iteration_count ? s.iteration_count.mean.toFixed(1) : "n/a"}</td>
                <td className="py-2 pr-4">{s.total_tokens ? s.total_tokens.mean.toFixed(0) : "n/a"}</td>
                <td className="py-2 pr-4">{s.estimated_cost_usd ? `$${s.estimated_cost_usd.mean.toFixed(6)}` : "n/a"}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function JobPicker() {
  // Results Explorer needs a benchmark_sweep job id; without one, point the
  // researcher at the Experiment Lab rather than fabricate placeholder data.
  return (
    <EmptyState
      title="No benchmark sweep selected"
      hint="Launch one from the Experiment Lab - you'll land here automatically with real results once it completes."
    />
  )
}

export function ResultsExplorer() {
  const { jobId } = useParams()
  const navigate = useNavigate()
  const jobQuery = useJob(jobId)
  const resultQuery = useBenchmarkResult(jobId, jobQuery.data?.status)

  if (!jobId) {
    return (
      <div>
        <TopBar title="Results Explorer" subtitle="Compare biological metrics, runtime, token consumption, and cost." />
        <div className="p-8"><JobPicker /></div>
      </div>
    )
  }

  const isRunning = jobQuery.data && !["completed", "failed"].includes(jobQuery.data.status)
  const summaries = resultQuery.data?.result?.summaries ?? []

  return (
    <div>
      <TopBar title="Results Explorer" subtitle={`Benchmark sweep ${jobId}`} />
      <div className="space-y-6 p-8">
        {jobQuery.isLoading && <LoadingState label="Loading sweep status..." />}
        {jobQuery.error && <ErrorState message={(jobQuery.error as Error).message} />}
        {jobQuery.data && (
          <div className="flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-3">
            <div className="flex items-center gap-3">
              <StatusBadge status={jobQuery.data.status} />
              <span className="text-sm text-slate-400">
                {isRunning
                  ? "Sweep is running - this updates automatically."
                  : jobQuery.data.error ?? `${jobQuery.data.experiment_ids.length} experiments completed.`}
              </span>
            </div>
            <button onClick={() => navigate("/reports")} className="btn-secondary text-xs">
              View raw records in Research Reports
            </button>
          </div>
        )}

        {resultQuery.isLoading && jobQuery.data?.status === "completed" && <LoadingState label="Aggregating results..." />}
        {resultQuery.error && <ErrorState message={(resultQuery.error as Error).message} />}

        {summaries.length > 0 && (
          <>
            <Card title="Summary"><SummaryTable summaries={summaries} /></Card>
            <Card title="F1 by configuration (mean ± std across repeats)"><F1Chart summaries={summaries} /></Card>
            <Card title="Precision vs. recall">
              <PrecisionRecallChart summaries={summaries} />
              <p className="mt-2 text-xs text-slate-500">
                Configurations with no scored runs (e.g. every repeat was rejected before reaching
                verification) are omitted here - see the summary table for their success rate.
              </p>
            </Card>
            <Card title="Runtime by configuration"><RuntimeChart summaries={summaries} /></Card>
          </>
        )}
      </div>
    </div>
  )
}
