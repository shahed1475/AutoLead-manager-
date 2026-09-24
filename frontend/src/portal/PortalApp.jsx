import { useEffect } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import SiteLayout from './site/SiteLayout'
import Landing from './site/Landing'
import Privacy from './site/Privacy'
import { Account, ForgotPassword, Login, Signup } from './auth/AuthPages'
import { usePortalInfo } from './hooks'

// The client link's public website: the HOM site, sign-up / log-in, and the
// step that opens each client's own private dashboard.

function ScrollManager() {
  const { pathname, hash } = useLocation()
  useEffect(() => {
    if (hash) {
      const el = document.getElementById(hash.slice(1))
      if (el) { el.scrollIntoView({ behavior: 'smooth' }); return }
    }
    window.scrollTo(0, 0)
  }, [pathname, hash])
  return null
}

function Title() {
  const { name } = usePortalInfo()
  const { pathname } = useLocation()
  useEffect(() => {
    const page = { '/login': 'Log in', '/signup': 'Create your account', '/forgot-password': 'Reset password', '/privacy': 'Privacy', '/account': 'Your dashboard' }[pathname]
    document.title = page ? `${page} · ${name}` : `${name} — Find the right leads. Reach the people who decide.`
  }, [pathname, name])
  return null
}

function SignedOutListener() {
  const qc = useQueryClient()
  useEffect(() => {
    const onOut = () => qc.removeQueries({ queryKey: ['portal-me'] })
    window.addEventListener('portal:signed-out', onOut)
    return () => window.removeEventListener('portal:signed-out', onOut)
  }, [qc])
  return null
}

export default function PortalApp() {
  return (
    <BrowserRouter>
      <ScrollManager />
      <Title />
      <SignedOutListener />
      <Routes>
        <Route path="/" element={<SiteLayout><Landing /></SiteLayout>} />
        <Route path="/privacy" element={<SiteLayout><Privacy /></SiteLayout>} />
        <Route path="/login" element={<Login />} />
        <Route path="/signup" element={<Signup />} />
        <Route path="/forgot-password" element={<ForgotPassword />} />
        <Route path="/account" element={<Account />} />
        <Route path="/signin/*" element={<Navigate to="/login" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
