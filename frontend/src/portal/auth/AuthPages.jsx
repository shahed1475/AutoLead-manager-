import { useEffect, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Check, RefreshCw } from 'lucide-react'
import { LogoMark } from '../../components/Logo'
import { portalApi, getToken, setToken } from '../api'
import { usePortalInfo, useMe } from '../hooks'
import { Brand, ThemeSwitch } from '../site/SiteLayout'
import { CodeForm, FormError, Notice, PasswordField } from './fields'
import Onboarding from './Onboarding'

// Log in, sign up, forgot password, and "opening your dashboard" — the
// client link's account pages.

function AuthLayout({ title, subtitle, children, back = true }) {
  const { name } = usePortalInfo()
  return (
    <div className="min-h-[100dvh] bg-background grid lg:grid-cols-[1fr_minmax(0,34rem)]">
      <div className="flex flex-col px-4 sm:px-8 pt-safe pb-safe">
        <header className="h-16 flex items-center justify-between max-w-md w-full mx-auto lg:mx-0 lg:max-w-none">
          <Brand />
          <ThemeSwitch />
        </header>
        <main id="main" className="flex-1 flex items-center py-8">
          <div className="w-full max-w-sm mx-auto animate-page-in">
            {back && (
              <Link to="/" className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground mb-8">
                <ArrowLeft size={14} /> Back to {name}
              </Link>
            )}
            <h1 className="text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-foreground text-balance">{title}</h1>
            {subtitle && <p className="text-support mt-2">{subtitle}</p>}
            <div className="mt-8">{children}</div>
          </div>
        </main>
      </div>
      <aside className="hidden lg:flex flex-col justify-between bg-primary text-primary-foreground p-12" aria-hidden="true">
        <LogoMark size={40} />
        <div>
          <p className="text-3xl font-semibold tracking-tight leading-tight text-balance">Find the right leads. Reach the people who decide.</p>
          <ul className="mt-8 space-y-3 text-[15px] opacity-90">
            {['Leads found and researched for you', 'Decision makers with their job titles', 'Personal drafts you approve', 'A private workspace for your account'].map((t) => (
              <li key={t} className="flex items-center gap-2.5"><Check size={17} />{t}</li>
            ))}
          </ul>
        </div>
        <p className="text-sm opacity-70">{name} · Sales Growth Engine</p>
      </aside>
    </div>
  )
}

function useSignedIn() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  return (result) => {
    setToken(result.token)
    qc.setQueryData(['portal-me'], result.client)
    navigate('/account', { replace: true })
  }
}

// ── Log in ────────────────────────────────────────────────────────────────

export function Login() {
  const [params] = useSearchParams()
  const qc = useQueryClient()
  const [email, setEmail] = useState(params.get('email') || '')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [noPassword, setNoPassword] = useState(false)
  const [ready, setReady] = useState(!params.has('signout'))
  const signedIn = useSignedIn()
  const navigate = useNavigate()
  const info = usePortalInfo()

  // Signed out from inside the dashboard (?signout=1): end this session too.
  useEffect(() => {
    if (!params.has('signout')) {
      if (getToken()) navigate('/account', { replace: true })
      return
    }
    portalApi.signOut().catch(() => {}).finally(() => {
      setToken(null); qc.clear(); setReady(true)
      window.history.replaceState(null, '', '/login')
    })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const login = useMutation({
    mutationFn: () => portalApi.login(email.trim(), password),
    onSuccess: signedIn,
    onError: (e) => { setError(e.message); setNoPassword(e.status === 409) },
  })
  if (!ready) return null
  return (
    <AuthLayout title="Welcome back" subtitle={`Log in to your ${info.name} account.`}>
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); setError(''); login.mutate() }}>
        {params.get('from') === 'workspace' && <Notice>Please log in again to open your dashboard.</Notice>}
        <FormError message={error} />
        {noPassword && (
          <Link to={`/forgot-password?email=${encodeURIComponent(email.trim())}`} className="btn-secondary w-full h-11">Set my password</Link>
        )}
        <div>
          <label htmlFor="login-email" className="label">Email</label>
          <input id="login-email" type="email" inputMode="email" autoComplete="email" required autoFocus={!email}
            className="input h-11" placeholder="you@company.com" value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div>
          <PasswordField id="login-password" value={password} onChange={setPassword} autoFocus={!!email} />
          <div className="mt-2 text-right">
            <Link to={`/forgot-password${email ? `?email=${encodeURIComponent(email.trim())}` : ''}`} className="text-sm font-medium text-primary hover:underline">Forgot password?</Link>
          </div>
        </div>
        <button type="submit" className="btn-primary w-full h-11" disabled={login.isPending || !email.trim() || !password}>
          {login.isPending && <RefreshCw size={14} className="animate-spin" />} Log in
        </button>
      </form>
      <p className="mt-8 text-sm text-muted-foreground text-center">
        New to {info.name}? <Link to="/signup" className="font-semibold text-primary hover:underline">Create an account</Link>
      </p>
    </AuthLayout>
  )
}

// ── Sign up ───────────────────────────────────────────────────────────────

export function Signup() {
  const info = usePortalInfo()
  const navigate = useNavigate()
  const [form, setForm] = useState({ name: '', company: '', email: '', password: '' })
  const [step, setStep] = useState('details')
  const [error, setError] = useState('')
  const signedIn = useSignedIn()
  useEffect(() => { if (getToken()) navigate('/account', { replace: true }) }, []) // eslint-disable-line react-hooks/exhaustive-deps
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: typeof e === 'string' ? e : e.target.value }))

  const start = useMutation({
    mutationFn: () => portalApi.signup({ ...form, email: form.email.trim() }),
    onSuccess: (r) => { setForm((f) => ({ ...f, email: r.email })); setStep('code'); setError('') },
    onError: (e) => setError(e.message),
  })
  const verify = useMutation({
    mutationFn: (code) => portalApi.verify(form.email, code),
    onSuccess: signedIn,
    onError: (e) => setError(e.message),
  })

  const closed = info.signup_mode === 'closed' || info.sign_in_ready === false
  if (step === 'code') {
    return (
      <AuthLayout title="Confirm your email" back={false}>
        <div className="space-y-4">
          <FormError message={error} />
          <CodeForm email={form.email} submitLabel="Create my account" busy={verify.isPending}
            onSubmit={(code) => { setError(''); verify.mutate(code) }}
            onResend={() => start.mutate()} resending={start.isPending}
            onBack={() => { setStep('details'); setError('') }} />
        </div>
      </AuthLayout>
    )
  }
  return (
    <AuthLayout title="Create your account" subtitle="Free during early access. No card needed.">
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); setError(''); start.mutate() }}>
        {closed && <Notice>{info.signup_mode === 'closed' ? 'New sign-ups are closed right now. Please check back soon.' : 'Sign-up opens shortly — please check back soon.'}</Notice>}
        {info.signup_mode === 'invite' && !closed && <Notice>Sign-up is by invitation. Use the email address you were invited with.</Notice>}
        <FormError message={error} />
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor="su-name" className="label">Your name</label>
            <input id="su-name" className="input h-11" autoComplete="name" required autoFocus value={form.name} onChange={set('name')} />
          </div>
          <div>
            <label htmlFor="su-company" className="label">Company <span className="font-normal">(optional)</span></label>
            <input id="su-company" className="input h-11" autoComplete="organization" value={form.company} onChange={set('company')} />
          </div>
        </div>
        <div>
          <label htmlFor="su-email" className="label">Work email</label>
          <input id="su-email" type="email" inputMode="email" autoComplete="email" required className="input h-11"
            placeholder="you@company.com" value={form.email} onChange={set('email')} />
        </div>
        <PasswordField id="su-password" label="Create a password" value={form.password} onChange={set('password')}
          autoComplete="new-password" showStrength />
        <button type="submit" className="btn-primary w-full h-11"
          disabled={closed || start.isPending || !form.name.trim() || !form.email.trim() || form.password.length < 10}>
          {start.isPending && <RefreshCw size={14} className="animate-spin" />} Continue
        </button>
        <p className="text-meta text-center">We’ll email you a code to confirm it’s you. By continuing you agree to our <Link to="/privacy" className="underline hover:text-foreground">privacy notice</Link>.</p>
      </form>
      <p className="mt-8 text-sm text-muted-foreground text-center">
        Already have an account? <Link to="/login" className="font-semibold text-primary hover:underline">Log in</Link>
      </p>
    </AuthLayout>
  )
}

// ── Forgot password (also: set a first password) ──────────────────────────

export function ForgotPassword() {
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [email, setEmail] = useState(params.get('email') || '')
  const [step, setStep] = useState('email')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')

  const send = useMutation({
    mutationFn: () => portalApi.requestCode(email.trim(), 'reset'),
    onSuccess: (r) => { setEmail(r.email); setStep('code'); setError('') },
    onError: (e) => setError(e.message),
  })
  const verify = useMutation({
    mutationFn: (code) => portalApi.verify(email, code),
    onSuccess: (r) => { setToken(r.token); qc.setQueryData(['portal-me'], r.client); setStep('password'); setError('') },
    onError: (e) => setError(e.message),
  })
  const save = useMutation({
    mutationFn: () => portalApi.setPassword(password),
    onSuccess: () => navigate('/account', { replace: true }),
    onError: (e) => setError(e.message),
  })

  if (step === 'code') {
    return (
      <AuthLayout title="Check your email" back={false}>
        <div className="space-y-4">
          <FormError message={error} />
          <CodeForm email={email} submitLabel="Continue" busy={verify.isPending}
            onSubmit={(code) => { setError(''); verify.mutate(code) }}
            onResend={() => send.mutate()} resending={send.isPending} onBack={() => setStep('email')} />
          <p className="text-meta">If there’s no account for this email, no code is sent.</p>
        </div>
      </AuthLayout>
    )
  }
  if (step === 'password') {
    return (
      <AuthLayout title="Choose a new password" subtitle="You’ll use it to log in from now on." back={false}>
        <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); setError(''); save.mutate() }}>
          <FormError message={error} />
          <PasswordField id="new-password" label="New password" value={password} onChange={setPassword}
            autoComplete="new-password" showStrength autoFocus />
          <button type="submit" className="btn-primary w-full h-11" disabled={save.isPending || password.length < 10}>
            {save.isPending && <RefreshCw size={14} className="animate-spin" />} Save and continue
          </button>
        </form>
      </AuthLayout>
    )
  }
  return (
    <AuthLayout title="Reset your password" subtitle="Enter your email and we’ll send you a code. This also works if you haven’t set a password yet.">
      <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); setError(''); send.mutate() }}>
        <FormError message={error} />
        <div>
          <label htmlFor="fp-email" className="label">Email</label>
          <input id="fp-email" type="email" inputMode="email" autoComplete="email" required autoFocus className="input h-11"
            placeholder="you@company.com" value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <button type="submit" className="btn-primary w-full h-11" disabled={send.isPending || !email.trim()}>
          {send.isPending && <RefreshCw size={14} className="animate-spin" />} Send code
        </button>
      </form>
      <p className="mt-8 text-sm text-muted-foreground text-center">
        Remembered it? <Link to="/login" className="font-semibold text-primary hover:underline">Log in</Link>
      </p>
    </AuthLayout>
  )
}

// ── After sign-in: your dashboard ─────────────────────────────────────────

const WAIT_COPY = {
  CREATING: ['Setting up your dashboard', 'Your private workspace is being prepared. This takes about a minute the first time.'],
  STARTING: ['Starting your dashboard', 'Almost there…'],
  WAITING_FOR_RELEASE: ['Setting up your dashboard', 'Your workspace is being prepared. Please check back in a few minutes.'],
  WAITLIST: ["You're on the list", 'All dashboards are in use right now. You’ll get yours as soon as a place opens — just log in again later.'],
  STOPPED: ['Your dashboard is paused', 'Contact us if you need access again.'],
  OFFLINE: ["Your dashboard isn't available right now", 'Please try again in a few minutes.'],
  ERROR: ["Your dashboard isn't available right now", 'Please try again in a few minutes.'],
  NONE: ['Setting up your dashboard', 'One moment…'],
}

export function Account() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { data: me, isError } = useMe()
  const [entering, setEntering] = useState(false)
  useEffect(() => { if (!getToken() || isError) navigate('/login', { replace: true }) }, [isError]) // eslint-disable-line react-hooks/exhaustive-deps
  // Accounts from before passwords existed set one first.
  useEffect(() => {
    if (me && me.has_password === false) navigate(`/forgot-password?email=${encodeURIComponent(me.email)}`, { replace: true })
  }, [me]) // eslint-disable-line react-hooks/exhaustive-deps
  const ws = useQuery({
    queryKey: ['portal-workspace'], queryFn: portalApi.workspace, enabled: !!me && !me.needs_onboarding && me.has_password !== false,
    refetchInterval: (q) => (q.state.data?.state === 'RUNNING' ? false : 4000),
  })
  const enter = useMutation({
    mutationFn: portalApi.enter,
    onSuccess: (r) => window.location.replace(r.url),
    onError: () => setEntering(false),
  })
  const state = ws.data?.state
  useEffect(() => {
    if (state === 'RUNNING' && !entering) { setEntering(true); enter.mutate() }
  }, [state]) // eslint-disable-line react-hooks/exhaustive-deps

  async function signOut() {
    try { await portalApi.signOut() } catch { /* already signed out */ }
    setToken(null); qc.clear(); navigate('/', { replace: true })
  }

  if (!me) return <div className="min-h-[100dvh] grid place-items-center bg-background"><RefreshCw className="animate-spin text-muted-foreground" size={20} /></div>
  if (me.has_password === false) return null
  if (me.needs_onboarding) return <Onboarding me={me} onSignOut={signOut} />

  const [title, text] = !state || state === 'RUNNING' ? ['Opening your dashboard', 'One moment…'] : (WAIT_COPY[state] || WAIT_COPY.NONE)
  const busy = !state || ['RUNNING', 'CREATING', 'STARTING', 'NONE', 'WAITING_FOR_RELEASE'].includes(state)
  return (
    <AuthLayout title={title} back={false}>
      <div role="status" aria-live="polite" className="space-y-6">
        <p className="text-support">{enter.error ? enter.error.message : text}</p>
        {busy && !enter.error && <RefreshCw size={20} className="animate-spin text-muted-foreground" />}
        {enter.error && <button className="btn-secondary h-10" onClick={() => { setEntering(false); ws.refetch() }}>Try again</button>}
        <p className="text-sm text-muted-foreground pt-4 border-t border-border-subtle">
          Signed in as <span className="text-foreground">{me.email}</span> · <button type="button" onClick={signOut} className="font-medium text-primary hover:underline">Sign out</button>
        </p>
      </div>
    </AuthLayout>
  )
}
