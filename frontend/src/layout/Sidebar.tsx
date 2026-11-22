import { NavLink } from "react-router-dom"

const NAV_ITEMS = [
  { to: "/", label: "Research Workspace", icon: "🧬", end: true },
  { to: "/observatory", label: "Agent Observatory", icon: "🔭" },
  { to: "/lab", label: "Experiment Lab", icon: "⚗️" },
  { to: "/results", label: "Results Explorer", icon: "📊" },
  { to: "/reports", label: "Research Reports", icon: "📄" },
]

export function Sidebar() {
  return (
    <aside className="flex w-60 shrink-0 flex-col border-r border-slate-800 bg-slate-950/60">
      <div className="px-5 py-5">
        <p className="text-lg font-semibold tracking-tight text-slate-100">HELIX</p>
        <p className="text-xs text-slate-500">Multi-agent research platform</p>
      </div>
      <nav className="flex-1 space-y-0.5 px-3">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) =>
              `flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                isActive
                  ? "bg-slate-800 text-slate-100"
                  : "text-slate-400 hover:bg-slate-900 hover:text-slate-200"
              }`
            }
          >
            <span aria-hidden>{item.icon}</span>
            {item.label}
          </NavLink>
        ))}
      </nav>
      <div className="border-t border-slate-800 px-5 py-4 text-xs text-slate-600">
        Synthetic data only. Not a clinical tool.
      </div>
    </aside>
  )
}
