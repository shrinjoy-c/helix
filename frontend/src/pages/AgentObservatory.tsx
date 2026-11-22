import { useNavigate, useParams } from "react-router-dom"
import type { EventLogEntry, ExperimentRecord } from "../api/types"
import { Card, StatTile } from "../components/Card"
import { EmptyState, ErrorState, LoadingState } from "../components/States"
import { StatusBadge } from "../components/StatusBadge"
import { useExperiment, useExperiments, useJob } from "../hooks/useApi"
import { TopBar } from "../layout/TopBar"

const ACTOR_COLORS: Record<string, string> = {
  principal_investigator: "border-violet-500/40 text-violet-300",
  strategist: "border-cyan-500/40 text-cyan-300",
  experiment_designer: "border-amber-500/40 text-amber-300",
  implementation: "border-emerald-500/40 text-emerald-300",
  reviewer: "border-rose-500/40 text-rose-300",
  verifier: "border-sky-500/40 text-sky-300",
  single_agent: "border-fuchsia-500/40 text-fuchsia-300",
  orchestrator: "border-slate-500/40 text-slate-300",
}

function actorStyle(actor: string) {
  return ACTOR_COLORS[actor] ?? "border-slate-500/40 text-slate-300"
}

function EventPayload({ event }: { event: EventLogEntry }) {
  const hasPayload = event.payload && Object.keys(event.payload).length > 0
  if (!hasPayload) return null
  return (
    <details className="mt-1.5">
      <summary className="cursor-pointer text-xs text-slate-500 hover:text-slate-400">details</summary>
      <pre className="mt-1.5 overflow-x-auto rounded-md bg-slate-950 p-2.5 text-xs text-slate-400">
        {JSON.stringify(event.payload, null, 2)}
      </pre>
    </details>
  )
}

function EventTimeline({ events }: { events: EventLogEntry[] }) {
  if (events.length === 0) {
    return <EmptyState title="No events yet" hint="Events will appear here as agents act." />
  }
  return (
    <ol className="space-y-0">
      {events.map((event, i) => (
        <li key={event.sequence} className="relative pb-5 pl-6">
          {i < events.length - 1 && (
            <span className="absolute left-[7px] top-4 h-full w-px bg-slate-800" />
          )}
          <span className="absolute left-0 top-1.5 h-3.5 w-3.5 rounded-full border-2 border-slate-950 bg-slate-600" />
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-xs text-slate-600">#{event.sequence}</span>
            <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${actorStyle(event.actor)}`}>
              {event.actor}
            </span>
            <span className="rounded border border-slate-700 px-1.5 py-0.5 font-mono text-[11px] text-slate-500">
              {event.event_type}
            </span>
          </div>
          <p className="mt-1 text-sm text-slate-300">{event.message}</p>
          <EventPayload event={event} />
        </li>
      ))}
    </ol>
  )
}

function ExperimentDetail({ experiment }: { experiment: ExperimentRecord }) {
  const metrics = experiment.evaluation_metrics
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-5 gap-3">
        <StatTile label="Status" value={<StatusBadge status={experiment.status} />} />
        <StatTile label="Iterations" value={experiment.iteration_count ?? "-"} />
        <StatTile label="Runtime" value={`${experiment.runtime_seconds.toFixed(3)}s`} />
        <StatTile
          label="Tokens"
          value={
            experiment.token_usage
              ? experiment.token_usage.input_tokens + experiment.token_usage.output_tokens
              : "-"
          }
        />
        <StatTile label="Est. cost" value={`$${(experiment.estimated_cost_usd ?? 0).toFixed(6)}`} />
      </div>

      {experiment.ablation && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-2 text-sm text-amber-300">
          Ablation active: <span className="font-mono">{experiment.ablation}</span>
        </div>
      )}

      {metrics && (
        <Card title="Biological metrics (independent evaluation)">
          <div className="grid grid-cols-4 gap-3">
            <StatTile label="Precision" value={metrics.precision.toFixed(2)} />
            <StatTile label="Recall" value={metrics.recall.toFixed(2)} />
            <StatTile label="F1" value={metrics.f1.toFixed(2)} />
            <StatTile
              label="Abundance MAE"
              value={metrics.abundance_mae !== null ? metrics.abundance_mae.toFixed(4) : "n/a"}
            />
          </div>
        </Card>
      )}

      <Card title="Agent event log">
        <EventTimeline events={experiment.event_log ?? []} />
      </Card>
    </div>
  )
}

function JobPicker() {
  const navigate = useNavigate()
  const { data: experiments } = useExperiments()
  if (!experiments || experiments.length === 0) {
    return (
      <EmptyState
        title="No experiments yet"
        hint="Launch one from the Research Workspace to see agent activity here."
      />
    )
  }
  return (
    <Card title="Recent experiments">
      <div className="space-y-2">
        {[...experiments].reverse().map((exp) => (
          <button
            key={exp.experiment_id}
            onClick={() => navigate(`/observatory/${exp.experiment_id}`)}
            className="flex w-full items-center justify-between rounded-lg border border-slate-800 bg-slate-950/40 px-4 py-2.5 text-left hover:border-slate-700"
          >
            <div>
              <p className="font-mono text-sm text-slate-200">{exp.experiment_id}</p>
              <p className="text-xs text-slate-500">{exp.pipeline_name} · {exp.dataset_id}</p>
            </div>
            <StatusBadge status={exp.status} />
          </button>
        ))}
      </div>
    </Card>
  )
}

export function AgentObservatory() {
  const { jobId } = useParams()
  // Recomputed every render (not useState) - this route also matches
  // /observatory/:jobId when the param is an experiment_id (from the recent-
  // experiments picker), and React Router reuses this component instance
  // across navigations between the two shapes without remounting it.
  const useDirectExperimentId = jobId?.startsWith("exp_") ?? false

  const jobQuery = useJob(useDirectExperimentId ? undefined : jobId)
  const experimentIdFromJob = jobQuery.data?.experiment_ids?.[0]
  const experimentId = useDirectExperimentId ? jobId : experimentIdFromJob
  const experimentQuery = useExperiment(experimentId)

  if (!jobId) {
    return (
      <div>
        <TopBar title="Agent Observatory" subtitle="Inspect agent roles, tasks, messages, and verification feedback." />
        <div className="p-8"><JobPicker /></div>
      </div>
    )
  }

  const isPolling = !useDirectExperimentId && jobQuery.data && !["completed", "failed"].includes(jobQuery.data.status)

  return (
    <div>
      <TopBar title="Agent Observatory" subtitle={`Job ${jobId}`} />
      <div className="space-y-6 p-8">
        {!useDirectExperimentId && jobQuery.isLoading && <LoadingState label="Loading job..." />}
        {!useDirectExperimentId && jobQuery.error && <ErrorState message={(jobQuery.error as Error).message} />}
        {!useDirectExperimentId && jobQuery.data && (
          <div className="flex items-center gap-3 rounded-lg border border-slate-800 bg-slate-900/40 px-4 py-3">
            <StatusBadge status={jobQuery.data.status} />
            <span className="text-sm text-slate-400">
              {isPolling ? "Agents are working - this updates automatically." : jobQuery.data.error ?? "Finished."}
            </span>
          </div>
        )}

        {experimentQuery.isLoading && <LoadingState label="Loading experiment detail..." />}
        {experimentQuery.error && <ErrorState message={(experimentQuery.error as Error).message} />}
        {experimentQuery.data && <ExperimentDetail experiment={experimentQuery.data} />}
      </div>
    </div>
  )
}
