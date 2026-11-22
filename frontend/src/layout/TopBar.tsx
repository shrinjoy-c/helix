import { useQuery } from "@tanstack/react-query"
import { api } from "../api/client"

export function TopBar({ title, subtitle }: { title: string; subtitle?: string }) {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: api.health,
    retry: false,
    refetchInterval: 15_000,
  })

  const connected = health.data?.status === "ok"

  return (
    <header className="flex items-center justify-between border-b border-slate-800 bg-slate-950/40 px-8 py-4">
      <div>
        <h1 className="text-xl font-semibold text-slate-100">{title}</h1>
        {subtitle && <p className="mt-0.5 text-sm text-slate-500">{subtitle}</p>}
      </div>
      <div className="flex items-center gap-2 text-xs">
        <span
          className={`h-2 w-2 rounded-full ${
            health.isLoading ? "bg-slate-600" : connected ? "bg-emerald-400" : "bg-rose-400"
          }`}
        />
        <span className="text-slate-500">
          {health.isLoading ? "Checking backend..." : connected ? "Backend connected" : "Backend unreachable"}
        </span>
      </div>
    </header>
  )
}
