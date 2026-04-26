import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Sidebar from './components/Sidebar'
import Topbar from './components/Topbar'
import Dashboard from './pages/Dashboard'
import Leads from './pages/Leads'
import Campaign from './pages/Campaign'
import AILab from './pages/AILab'
import Settings from './pages/Settings'
import Inbox from './pages/Inbox'

export default function App() {
  return (
    <BrowserRouter>
      <div className="flex h-screen overflow-hidden bg-slate-950">
        <Sidebar />
        <div className="flex-1 flex flex-col overflow-hidden">
          <Topbar />
          <main className="flex-1 overflow-y-auto">
            <Routes>
              <Route path="/" element={<Navigate to="/dashboard" replace />} />
              <Route path="/dashboard" element={<Dashboard />} />
              <Route path="/leads"     element={<Leads />} />
              <Route path="/campaign"  element={<Campaign />} />
              <Route path="/ai-lab"    element={<AILab />} />
              <Route path="/inbox"     element={<Inbox />} />
              <Route path="/settings"  element={<Settings />} />
            </Routes>
          </main>
        </div>
      </div>
    </BrowserRouter>
  )
}
