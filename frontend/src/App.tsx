import { Navigate, Route, HashRouter as Router, Routes } from "react-router-dom"
import { Sidebar } from "./layout/Sidebar"
import { AgentObservatory } from "./pages/AgentObservatory"
import { ExperimentLab } from "./pages/ExperimentLab"
import { ResearchReports } from "./pages/ResearchReports"
import { ResearchWorkspace } from "./pages/ResearchWorkspace"
import { ResultsExplorer } from "./pages/ResultsExplorer"

export default function App() {
  return (
    <Router>
      <div className="flex h-screen bg-slate-950 text-slate-200">
        <Sidebar />
        <main className="flex-1 overflow-y-auto">
          <Routes>
            <Route path="/" element={<ResearchWorkspace />} />
            <Route path="/observatory" element={<AgentObservatory />} />
            <Route path="/observatory/:jobId" element={<AgentObservatory />} />
            <Route path="/lab" element={<ExperimentLab />} />
            <Route path="/results" element={<ResultsExplorer />} />
            <Route path="/results/:jobId" element={<ResultsExplorer />} />
            <Route path="/reports" element={<ResearchReports />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </Router>
  )
}
