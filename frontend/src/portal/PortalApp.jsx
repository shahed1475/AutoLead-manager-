import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, LogOut, RefreshCw, Sun, Moon, Check } from 'lucide-react'
import toast from 'react-hot-toast'
import clsx from 'clsx'
import { LogoMark, BRAND } from '../components/Logo'
import { useTheme } from '../lib/theme'
import { portalApi, getToken, setToken } from './api'

// HOM client portal — clients sign in with a code sent to their Gmail, ask
// for leads, follow progress and download what is delivered. It talks only to
// /api/portal/*; nothing of the owner's dashboard is part of this app.

function ThemeSwitch() {
  const { theme, toggle } = useTheme()
  return (
    <button type="button" onClick={toggle} className="btn-ghost h-9 w-9 px-0"
      aria-label={theme === 'light' ? 'Night mode' : 'Day mode'}>
      {theme === 'light' ? <Moon size={16} /> : <Sun size={16} />}
    </button>
  )
}

// The owner's portal settings (name, welcome text, contact, limits) — public.
function usePortalInfo() {
  const { data } = useQuery({ queryKey: ['portal-status'], queryFn: portalApi.status, refetchInterval: 60_000, staleTime: 30_000 })
  return data
}

function Shell({ me, onSignOut, children }) {
  const info = usePortalInfo()
  const name = info?.name || BRAND.name
  useEffect(() => { document.title = `${name} · Client portal` }, [name])
  return (
    <div className="min-h-[100dvh] bg-background flex flex-col">
      <header className="pt-safe border-b border-border-subtle bg-background/85 backdrop-blur-md sticky top-0 z-10">
        <div className="max-w-3xl mx-auto h-14 px-4 sm:px-6 flex items-center gap-3">
          <a href="/signin/" className="flex items-center gap-2.5">
            <LogoMark size={26} />
            <span className="text-subheading truncate max-w-[10rem] sm:max-w-none">{name}</span>
            <span className="text-meta hidden sm:inline">Client portal</span>
          </a>
          <div className="flex-1" />
          {me && <span className="text-meta hidden sm:inline truncate max-w-[14rem]">{me.email}</span>}
          <ThemeSwitch />
          {me && (
            <button type="button" onClick={onSignOut} className="btn-ghost h-9 px-3 text-sm">
              <LogOut size={15} /> <span className="hidden sm:inline">Sign out</span>
            </button>
          )}
        </div>
      </header>
      <main className="flex-1 w-full max-w-3xl mx-auto px-4 sm:px-6 py-8 pb-safe animate-page-in">{children}</main>
      {info?.contact_email && (
        <footer className="w-full max-w-3xl mx-auto px-4 sm:px-6 pb-8 text-meta text-center">
          Questions? Email <span className="text-foreground select-all">{info.contact_email}</span>
        </footer>
      )}
    </div>
  )
}

// ── Sign in ──────────────────────────────────────────────────────────────────

function SignIn({ onSignedIn }) {
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [step, setStep] = useState('email')
  const status = usePortalInfo()
  const closed = status && !status.sign_in_ready
  const [cooldown, setCooldown] = useState(0)

  useEffect(() => {
    if (!cooldown) return
    const t = setTimeout(() => setCooldown((c) => c - 1), 1000)
    return () => clearTimeout(t)
  }, [cooldown])

  const send = useMutation({
    mutationFn: () => portalApi.requestCode(email.trim()),
    onSuccess: (r) => { setEmail(r.email); setStep('code'); setCode(''); setCooldown(60) },
    onError: (e) => toast.error(e.message),
  })
  const verify = useMutation({
    mutationFn: () => portalApi.verify(email, code.trim()),
    onSuccess: (r) => { setToken(r.token); onSignedIn(r.client) },
    onError: (e) => toast.error(e.message),
  })

  return (
    <div className="max-w-sm mx-auto pt-6 sm:pt-12">
      <div className="text-center">
        <LogoMark size={52} className="mx-auto" />
        <h1 className="text-page mt-6 text-balance">{step === 'email' ? `Welcome to ${status?.name || BRAND.name}` : 'Check your email'}</h1>
        <p className="text-support mt-1.5">
          {step === 'email'
            ? (status?.signup_mode === 'invite'
              ? 'Sign in with the email address you were invited with.'
              : 'Sign in or create your account with your Gmail.')
            : <>We sent a 6-digit code to <span className="text-foreground font-medium">{email}</span>.</>}
        </p>
        {step === 'email' && status?.welcome && <p className="text-sm text-foreground mt-4 whitespace-pre-line">{status.welcome}</p>}
      </div>

      {step === 'email' ? (
        <form className="surface-overlay mt-8 p-5 space-y-3" onSubmit={(e) => { e.preventDefault(); send.mutate() }}>
          {closed && (
            <p role="status" className="rounded-lg bg-warning/10 text-sm text-foreground px-3 py-2.5">
              {status.signup_mode === 'closed'
                ? 'Sign-in is closed right now. Please check back later.'
                : "Sign-in isn't open yet — we're still setting it up. Please check back soon."}
            </p>
          )}
          <label htmlFor="portal-email" className="label">Your Gmail address</label>
          <input id="portal-email" type="email" inputMode="email" autoComplete="email" autoFocus required
            className="input" placeholder="you@gmail.com" value={email} onChange={(e) => setEmail(e.target.value)} />
          <button type="submit" className="btn-primary w-full" disabled={send.isPending || !email.trim() || closed}>
            {send.isPending && <RefreshCw size={14} className="animate-spin" />} Send me a code
          </button>
          {status?.signup_mode !== 'invite' && <p className="text-meta text-center">New here? Your account is created when you sign in.</p>}
        </form>
      ) : (
        <form className="surface-overlay mt-8 p-5 space-y-3" onSubmit={(e) => { e.preventDefault(); verify.mutate() }}>
          <label htmlFor="portal-code" className="label">6-digit code</label>
          <input id="portal-code" inputMode="numeric" autoComplete="one-time-code" autoFocus maxLength={6}
            className="input text-center tracking-[0.5em] text-lg tabular" placeholder="••••••"
            value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))} />
          <button type="submit" className="btn-primary w-full" disabled={verify.isPending || code.length !== 6}>
            {verify.isPending && <RefreshCw size={14} className="animate-spin" />} Sign in
          </button>
          <div className="flex items-center justify-between text-sm pt-1">
            <button type="button" className="text-muted-foreground hover:text-foreground" onClick={() => setStep('email')}>
              Use a different email
            </button>
            <button type="button" className="font-semibold text-primary disabled:text-muted-foreground"
              disabled={cooldown > 0 || send.isPending} onClick={() => send.mutate()}>
              {cooldown > 0 ? `Resend in ${cooldown}s` : 'Resend code'}
            </button>
          </div>
          <p className="text-meta text-center">Can't find it? Check your spam folder.</p>
        </form>
      )}
    </div>
  )
}

function Profile({ me, onSaved }) {
  const [name, setName] = useState(me.name || '')
  const [company, setCompany] = useState(me.company || '')
  const save = useMutation({
    mutationFn: () => portalApi.updateMe({ name, company }),
    onSuccess: onSaved,
    onError: (e) => toast.error(e.message),
  })
  return (
    <div className="max-w-sm mx-auto pt-6">
      <h1 className="text-page">Nice to meet you</h1>
      <p className="text-support mt-1.5">Tell us who we're working with.</p>
      <form className="surface-overlay mt-6 p-5 space-y-4" onSubmit={(e) => { e.preventDefault(); save.mutate() }}>
        <div>
          <label htmlFor="p-name" className="label">Your name</label>
          <input id="p-name" className="input" autoFocus autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <label htmlFor="p-company" className="label">Company <span className="font-normal">(optional)</span></label>
          <input id="p-company" className="input" autoComplete="organization" value={company} onChange={(e) => setCompany(e.target.value)} />
        </div>
        <button type="submit" className="btn-primary w-full" disabled={save.isPending || !name.trim()}>Continue</button>
      </form>
    </div>
  )
}

// ── Requests ─────────────────────────────────────────────────────────────────

const WAIT_COPY = {
  CREATING: ['Setting up your dashboard', 'Your own private workspace is being prepared. This takes about a minute the first time.'],
  STARTING: ['Starting your dashboard', 'Almost there…'],
  WAITING_FOR_RELEASE: ['Setting up your dashboard', 'Your workspace is being prepared. Please check back in a few minutes.'],
  WAITLIST: ["You're on the list", "All dashboards are in use right now. You'll get yours as soon as a place opens up — just sign in again later."],
  STOPPED: ['Your dashboard is paused', 'Contact us if you need access again.'],
  OFFLINE: ["Your dashboard isn't available right now", 'Please try again in a few minutes.'],
  ERROR: ["Your dashboard isn't available right now", "We've been notified. Please try again in a few minutes."],
  NONE: ['Setting up your dashboard', 'One moment…'],
}

// After sign-in: open the client's own dashboard as soon as it's ready.
function Workspace() {
  const [entering, setEntering] = useState(false)
  const fromWorkspace = new URLSearchParams(window.location.search).get('from') === 'workspace'
  const { data, isError, refetch } = useQuery({
    queryKey: ['portal-workspace'], queryFn: portalApi.workspace,
    refetchInterval: (q) => (q.state.data?.state === 'RUNNING' ? false : 4000),
  })
  const state = data?.state
  const enter = useMutation({
    mutationFn: portalApi.enter,
    onSuccess: (r) => { window.location.replace(r.url) },
    onError: (e) => { setEntering(false); toast.error(e.message) },
  })
  useEffect(() => {
    if (state === 'RUNNING' && !entering && !enter.isPending) { setEntering(true); enter.mutate() }
  }, [state]) // eslint-disable-line react-hooks/exhaustive-deps

  if (isError) {
    return (
      <div className="max-w-sm mx-auto pt-12 text-center space-y-4">
        <p className="text-support">We couldn't reach your dashboard.</p>
        <button className="btn-secondary" onClick={() => refetch()}><RefreshCw size={14} /> Try again</button>
      </div>
    )
  }
  const [title, text] = state === 'RUNNING' || !state
    ? ['Opening your dashboard', fromWorkspace ? 'Reconnecting…' : 'One moment…']
    : (WAIT_COPY[state] || WAIT_COPY.NONE)
  const busy = !state || ['RUNNING', 'CREATING', 'STARTING', 'NONE', 'WAITING_FOR_RELEASE'].includes(state)
  return (
    <div className="max-w-sm mx-auto pt-10 sm:pt-16 text-center" role="status" aria-live="polite">
      <LogoMark size={52} className="mx-auto" />
      <h1 className="text-page mt-6 text-balance">{title}</h1>
      <p className="text-support mt-2 text-balance">{text}</p>
      {busy && <RefreshCw size={18} className="animate-spin text-muted-foreground mx-auto mt-6" />}
    </div>
  )
}

export default function PortalApp() {
  const qc = useQueryClient()
  const [me, setMe] = useState(null)
  const [checking, setChecking] = useState(!!getToken())

  useEffect(() => {
    // Signed out from inside the dashboard (/signin/?signout=1): end this session too.
    const params = new URLSearchParams(window.location.search)
    if (params.has('signout')) {
      window.history.replaceState(null, '', '/signin/')
      portalApi.signOut().catch(() => {}).finally(() => { setToken(null); setChecking(false) })
      return
    }
    if (!getToken()) return
    portalApi.me().then(setMe).catch(() => setToken(null)).finally(() => setChecking(false))
  }, [])
  useEffect(() => {
    const onOut = () => { setMe(null); qc.clear() }
    window.addEventListener('portal:signed-out', onOut)
    return () => window.removeEventListener('portal:signed-out', onOut)
  }, [qc])

  async function signOut() {
    try { await portalApi.signOut() } catch { /* already signed out */ }
    setToken(null); setMe(null); qc.clear()
  }

  if (checking) return <div className="min-h-[100dvh] grid place-items-center bg-background"><RefreshCw className="animate-spin text-muted-foreground" size={20} /></div>

  return (
    <Shell me={me} onSignOut={signOut}>
      {!me ? <SignIn onSignedIn={setMe} />
        : me.needs_profile ? <Profile me={me} onSaved={setMe} />
          : <Workspace />}
    </Shell>
  )
}
