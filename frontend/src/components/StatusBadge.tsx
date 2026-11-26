const STYLES: Record<string, string> = {
  completed: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  running: "bg-sky-500/15 text-sky-400 border-sky-500/30",
  pending: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  failed: "bg-rose-500/15 text-rose-400 border-rose-500/30",
  needs_revision: "bg-amber-500/15 text-amber-400 border-amber-500/30",
  cancelled: "bg-slate-500/15 text-slate-400 border-slate-500/30",
  timed_out: "bg-orange-500/15 text-orange-400 border-orange-500/30",
  budget_exceeded: "bg-orange-500/15 text-orange-400 border-orange-500/30",
}

export function StatusBadge({ status }: { status: string }) {
  const style = STYLES[status] ?? "bg-slate-500/15 text-slate-400 border-slate-500/30"
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${style}`}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {status.replace(/_/g, " ")}
    </span>
  )
}
