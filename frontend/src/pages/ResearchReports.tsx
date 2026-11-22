import { useState } from "react"
import type { ExperimentRecord } from "../api/types"
import { Card } from "../components/Card"
import { EmptyState, ErrorState, LoadingState } from "../components/States"
import { StatusBadge } from "../components/StatusBadge"
import { useExperiments } from "../hooks/useApi"
import { TopBar } from "../layout/TopBar"

function downloadJson(record: ExperimentRecord) {
  const blob = new Blob([JSON.stringify(record, null, 2)], { type: "application/json" })
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = `${record.experiment_id}.json`
  a.click()
  URL.revokeObjectURL(url)
}

function ExperimentRow({ record }: { record: ExperimentRecord }) {
  const [expanded, setExpanded] = useState(false)
  const metrics = record.evaluation_metrics

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-950/40">
      <button
        onClick={() => setExpanded((e) => !e)}
        className="flex w-full items-center justify-between px-4 py-3 text-left"
      >
        <div className="flex items-center gap-3">
          <StatusBadge status={record.status} />
          <span className="font-mono text-sm text-slate-200">{record.experiment_id}</span>
          <span className="text-xs text-slate-500">{record.pipeline_name} · {record.dataset_id}</span>
        </div>
        <div className="flex items-center gap-4">
          {metrics && (
            <span className="font-mono text-xs text-slate-400">
              F1={metrics.f1.toFixed(2)}
            </span>
          )}
          <span className="text-xs text-slate-500">{new Date(record.created_at).toLocaleString()}</span>
          <span className="text-slate-500">{expanded ? "▾" : "▸"}</span>
        </div>
      </button>
      {expanded && (
        <div className="space-y-3 border-t border-slate-800 px-4 py-3">
          <div className="flex justify-end">
            <button
              onClick={(e) => { e.stopPropagation(); downloadJson(record) }}
              className="btn-secondary text-xs"
            >
              Download full record (JSON)
            </button>
          </div>
          <pre className="max-h-96 overflow-auto rounded-md bg-slate-950 p-3 text-xs text-slate-400">
            {JSON.stringify(record, null, 2)}
          </pre>
        </div>
      )}
    </div>
  )
}

export function ResearchReports() {
  const { data: experiments, isLoading, error } = useExperiments()

  return (
    <div>
      <TopBar title="Research Reports" subtitle="Inspect and export reproducible experiment results." />
      <div className="space-y-4 p-8">
        {isLoading && <LoadingState label="Loading experiment records..." />}
        {error && <ErrorState message={(error as Error).message} />}
        {experiments && experiments.length === 0 && (
          <EmptyState title="No experiments recorded yet" hint="Results appear here once you launch something from the Workspace or Lab." />
        )}
        {experiments && experiments.length > 0 && (
          <Card title={`${experiments.length} experiment record(s)`}>
            <div className="space-y-2">
              {[...experiments].reverse().map((record) => (
                <ExperimentRow key={record.experiment_id} record={record} />
              ))}
            </div>
          </Card>
        )}
      </div>
    </div>
  )
}
