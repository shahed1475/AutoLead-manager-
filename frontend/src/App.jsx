import { lazy, Suspense, useState } from 'react'
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import AuthGate from './components/AuthGate'
import Sidebar from './components/Sidebar'
import Topbar from './components/Topbar'
import ErrorBoundary from './components/ui/ErrorBoundary'
import { PageSkeleton } from './components/ui/Skeleton'

const Dashboard  = lazy(() => import('./pages/Dashboard'))
const Leads      = lazy(() => import('./pages/Leads'))
const Pipeline   = lazy(() => import('./pages/Pipeline'))
const LeadSearch    = lazy(() => import('./pages/LeadSearch'))
const LeadSearchAutomation = lazy(() => import('./pages/LeadSearchAutomation'))
const LeadSearchManual     = lazy(() => import('./pages/LeadSearchManual'))
const LeadRun              = lazy(() => import('./pages/LeadRun'))
const ResearchAgent = lazy(() => import('./pages/ResearchAgent'))
const Campaign      = lazy(() => import('./pages/Campaign'))
const EmailCampaigns = lazy(() => import('./pages/EmailCampaigns'))
const AILab      = lazy(() => import('./pages/AILab'))
const Inbox      = lazy(() => import('./pages/Inbox'))
const Settings   = lazy(() => import('./pages/Settings'))
// ActivityLogs is added in Group G once backend log filtering/export exists.

function RoutedContent() {
  // Keyed by pathname so a page-level render crash doesn't leave the
  // ErrorBoundary "stuck" showing its fallback after the user navigates away.
  const { pathname } = useLocation()
  return (
    <ErrorBoundary key={pathname}>
      <Suspense fallback={<PageSkeleton />}>
        <div key={pathname} className="animate-page-in">
        <Routes>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/leads"     element={<Leads />} />
          <Route path="/pipeline"  element={<Pipeline />} />
          <Route path="/lead-search" element={<LeadSearch />} />
          <Route path="/lead-search/automation" element={<LeadSearchAutomation />} />
          <Route path="/lead-search/manual" element={<LeadSearchManual />} />
          <Route path="/lead-search/runs/:runId" element={<LeadRun />} />
          <Route path="/research-agent" element={<ResearchAgent />} />
          <Route path="/campaign"  element={<Campaign />} />
          <Route path="/email-campaigns" element={<EmailCampaigns />} />
          <Route path="/ai-lab"    element={<AILab />} />
          <Route path="/inbox"     element={<Inbox />} />
          <Route path="/settings"  element={<Settings />} />
        </Routes>
        </div>
      </Suspense>
    </ErrorBoundary>
  )
}

export default function App() {
  const [navOpen, setNavOpen] = useState(false)
  return (
    <AuthGate>
      <BrowserRouter>
        <div className="app-shell flex h-screen overflow-hidden">
          <Sidebar open={navOpen} onClose={() => setNavOpen(false)} />
          <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
            <Topbar onMenu={() => setNavOpen(true)} />
            <main className="flex-1 overflow-y-auto">
              <RoutedContent />
            </main>
          </div>
        </div>
      </BrowserRouter>
    </AuthGate>
  )
}
