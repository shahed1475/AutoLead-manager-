import { lazy, Suspense } from 'react'
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import AuthGate from './components/AuthGate'
import Sidebar from './components/Sidebar'
import Topbar from './components/Topbar'
import ErrorBoundary from './components/ui/ErrorBoundary'
import { PageSkeleton } from './components/ui/Skeleton'

const Dashboard = lazy(() => import('./pages/Dashboard'))
const Leads     = lazy(() => import('./pages/Leads'))
const Pipeline  = lazy(() => import('./pages/Pipeline'))
const Campaign  = lazy(() => import('./pages/Campaign'))
const AILab     = lazy(() => import('./pages/AILab'))
const Inbox     = lazy(() => import('./pages/Inbox'))
const Settings  = lazy(() => import('./pages/Settings'))
// ActivityLogs is added in Group G once backend log filtering/export exists.

function RoutedContent() {
  // Keyed by pathname so a page-level render crash doesn't leave the
  // ErrorBoundary "stuck" showing its fallback after the user navigates away.
  const { pathname } = useLocation()
  return (
    <ErrorBoundary key={pathname}>
      <Suspense fallback={<PageSkeleton />}>
        <Routes>
          <Route path="/" element={<Navigate to="/dashboard" replace />} />
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/leads"     element={<Leads />} />
          <Route path="/pipeline"  element={<Pipeline />} />
          <Route path="/campaign"  element={<Campaign />} />
          <Route path="/ai-lab"    element={<AILab />} />
          <Route path="/inbox"     element={<Inbox />} />
          <Route path="/settings"  element={<Settings />} />
        </Routes>
      </Suspense>
    </ErrorBoundary>
  )
}

export default function App() {
  return (
    <AuthGate>
      <BrowserRouter>
        <div className="flex h-screen overflow-hidden bg-slate-950">
          <Sidebar />
          <div className="flex-1 flex flex-col overflow-hidden">
            <Topbar />
            <main className="flex-1 overflow-y-auto">
              <RoutedContent />
            </main>
          </div>
        </div>
      </BrowserRouter>
    </AuthGate>
  )
}
