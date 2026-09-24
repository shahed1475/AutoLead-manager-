import { useState, useEffect, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Save, RefreshCw, Eye, EyeOff, Mail, Bot, Globe,
  MessageCircle, FileText, AlertTriangle, Shield, Zap,
  CheckCircle2, XCircle, Trash2, RotateCcw, Inbox,
  Lock, Unlock,
} from 'lucide-react'
import { settingsApi, aiApi, leadsApi, authApi, setSessionToken } from '../api/client'
import toast from 'react-hot-toast'
import clsx from 'clsx'
import EmailSendersSection from '../components/settings/EmailSendersSection'
import { isClientEdition } from '../lib/edition'
import SearchProvidersSection from '../components/settings/SearchProvidersSection'
import CompanyDnaEditor from '../components/settings/CompanyDnaEditor'

// ─── Sub-components ──────────────────────────────────────────────────────────

function SectionCard({ title, description, icon: Icon, iconColor = 'text-brand-400', children, actions }) {
  return (
    <div className="card overflow-hidden">
      <div className="flex items-start justify-between px-5 py-4 border-b border-slate-700/60">
        <div className="flex items-center gap-3">
          <span className={clsx('p-2 rounded-lg bg-slate-800', iconColor)}>
            <Icon size={16} />
          </span>
          <div>
            <h2 className="font-semibold text-slate-200 text-sm">{title}</h2>
            {description && <p className="text-xs text-slate-500 mt-0.5">{description}</p>}
          </div>
        </div>
        {actions && <div className="flex items-center gap-2 shrink-0 ml-4">{actions}</div>}
      </div>
      <div className="p-5 space-y-4">{children}</div>
    </div>
  )
}

function Field({ label, name, type = 'text', value, onChange, hint, placeholder, suffix }) {
  const [show, setShow] = useState(false)
  const isPassword = type === 'password'
  return (
    <div>
      <label className="label">{label}</label>
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <input
            type={isPassword && !show ? 'password' : 'text'}
            className={clsx('input', isPassword && 'pr-10')}
            value={value || ''}
            placeholder={placeholder}
            onChange={(e) => onChange && onChange(name, e.target.value)}
          />
          {isPassword && (
            <button
              type="button"
              onClick={() => setShow((v) => !v)}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-500 hover:text-slate-300"
            >
              {show ? <EyeOff size={13} /> : <Eye size={13} />}
            </button>
          )}
        </div>
        {suffix && <span className="text-xs text-slate-500 shrink-0 w-10">{suffix}</span>}
      </div>
      {hint && <p className="text-xs text-slate-500 mt-1.5 leading-relaxed">{hint}</p>}
    </div>
  )
}

function Toggle({ label, description, checked, onChange }) {
  return (
    <label className="flex items-start gap-3 cursor-pointer group select-none">
      <div
        onClick={onChange}
        className={clsx(
          'relative mt-0.5 w-10 h-5 rounded-full transition-colors shrink-0 cursor-pointer',
          checked ? 'bg-brand-500' : 'bg-slate-600'
        )}
      >
        <span
          className={clsx(
            'absolute top-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform duration-200',
            checked ? 'translate-x-5' : 'translate-x-0.5'
          )}
        />
      </div>
      <div>
        <p className="text-sm text-slate-300 group-hover:text-slate-200 transition-colors">{label}</p>
        {description && <p className="text-xs text-slate-500 mt-0.5">{description}</p>}
      </div>
    </label>
  )
}

function ConfirmModal({ action, onConfirm, onCancel }) {
  const [typed, setTyped] = useState('')
  const keyword = action === 'clear-leads' ? 'DELETE' : 'RESET'
  const title = action === 'clear-leads' ? 'Clear All Leads' : 'Reset Campaign Stats'
  const body = action === 'clear-leads'
    ? 'This will permanently delete ALL leads from the database. This cannot be undone.'
    : 'This will permanently delete all campaign logs and run history. This cannot be undone.'

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
      <div className="card w-full max-w-md p-6 space-y-5">
        <div className="flex items-start gap-4">
          <div className="p-2 rounded-lg bg-red-900/40 text-red-400 shrink-0">
            <AlertTriangle size={20} />
          </div>
          <div>
            <h3 className="font-semibold text-slate-100">{title}</h3>
            <p className="text-sm text-slate-400 mt-1">{body}</p>
          </div>
        </div>
        <div>
          <label className="label">
            Type <span className="font-mono text-red-400 font-bold">{keyword}</span> to confirm
          </label>
          <input
            className="input"
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={keyword}
            autoFocus
          />
        </div>
        <div className="flex gap-3">
          <button onClick={onCancel} className="btn-secondary flex-1 justify-center">
            Cancel
          </button>
          <button
            onClick={onConfirm}
            disabled={typed !== keyword}
            className={clsx(
              'flex-1 btn justify-center text-sm font-medium transition-colors',
              typed === keyword
                ? 'bg-red-600 hover:bg-red-700 text-white border-red-600 hover:border-red-700'
                : 'bg-slate-700 text-slate-500 border-slate-700 cursor-not-allowed'
            )}
          >
            Confirm
          </button>
        </div>
      </div>
    </div>
  )
}

function AppLockCard() {
  const [current,  setCurrent]  = useState('')
  const [next,     setNext]     = useState('')
  const [confirm,  setConfirm]  = useState('')

  const { data, refetch } = useQuery({
    queryKey: ['authStatus'],
    queryFn:  authApi.status,
  })
  const passwordSet = Boolean(data?.password_set)

  function reset() { setCurrent(''); setNext(''); setConfirm('') }

  const setMut = useMutation({
    mutationFn: () => authApi.setPassword(next, passwordSet ? current : undefined),
    onSuccess: ({ token }) => {
      setSessionToken(token)
      reset()
      refetch()
      toast.success(passwordSet ? 'Password changed' : 'App lock enabled')
    },
    onError: (e) => toast.error(e.message),
  })

  const clearMut = useMutation({
    mutationFn: () => authApi.clearPassword(current),
    onSuccess: () => {
      setSessionToken('')
      reset()
      refetch()
      toast.success('App lock disabled')
    },
    onError: (e) => toast.error(e.message),
  })

  function handleSave(e) {
    e.preventDefault()
    if (next.length < 4) return toast.error('Password must be at least 4 characters')
    if (next !== confirm) return toast.error('Passwords do not match')
    setMut.mutate()
  }

  if (isClientEdition()) return null     // client workspaces sign in through the client link
  return (
    <SectionCard
      title="App Lock"
      description="Optional local password — protects this app's API from anything else running on your machine."
      icon={Lock}
      iconColor="text-brand-400"
    >
      <form onSubmit={handleSave} className="space-y-4">
        {passwordSet && (
          <Field
            label="Current Password" name="current" type="password"
            value={current} onChange={(_, v) => setCurrent(v)}
            placeholder="Required to change or disable"
          />
        )}
        <div className="grid grid-cols-2 gap-4">
          <Field
            label={passwordSet ? 'New Password' : 'Set a Password'} name="next" type="password"
            value={next} onChange={(_, v) => setNext(v)} placeholder="At least 4 characters"
          />
          <Field
            label="Confirm" name="confirm" type="password"
            value={confirm} onChange={(_, v) => setConfirm(v)} placeholder="Repeat password"
          />
        </div>

        <div className="flex items-center gap-2">
          <button
            type="submit"
            disabled={setMut.isPending || !next}
            className="btn text-xs bg-brand-600 hover:bg-brand-500 text-white border-brand-600 disabled:opacity-40"
          >
            {setMut.isPending
              ? <><RefreshCw size={13} className="animate-spin" /> Saving…</>
              : <><Save size={13} /> {passwordSet ? 'Change Password' : 'Enable App Lock'}</>}
          </button>

          {passwordSet && (
            <button
              type="button"
              disabled={clearMut.isPending || !current}
              onClick={() => clearMut.mutate()}
              title="Requires current password"
              className="btn text-xs border-red-800/60 text-red-400 hover:bg-red-900/30 hover:border-red-600 disabled:opacity-40"
            >
              <Unlock size={13} /> Disable Lock
            </button>
          )}
        </div>
      </form>
    </SectionCard>
  )
}

// ─── Defaults ────────────────────────────────────────────────────────────────

const DEFAULTS = {
  smtp_host: 'smtp.gmail.com',
  smtp_port: '587',
  smtp_username: '',
  smtp_password: '',
  smtp_from_name: '',
  smtp_from_email: '',
  imap_host: 'imap.gmail.com',
  imap_port: '993',
  imap_username: '',
  imap_password: '',
  imap_ssl: 'true',
  ollama_base_url: 'http://localhost:11434',
  ollama_model: 'llama3',
  ollama_timeout: '120',
  whatsapp_wait_time: '15',
  whatsapp_close_tab: 'true',
  schedule_hour: '9',
  daily_email_limit: '50',
  daily_whatsapp_limit: '20',
  followup_delay_days: '3',
  auto_send_enabled: 'false',
  scraper_headless: 'true',
  scraper_delay_min: '2.0',
  scraper_delay_max: '5.0',
  discovery_enrichment_enabled: 'true',
  research_handoff_mode: 'manual',
  research_handoff_min_score: '60',
  llm_provider: 'ollama',
  openai_api_key: '',
  openai_model: 'gpt-4o-mini',
  anthropic_api_key: '',
  anthropic_model: 'claude-3-5-haiku-20241022',
}

// ─── Main component ───────────────────────────────────────────────────────────

export default function Settings() {
  const qc = useQueryClient()
  const [values, setValues] = useState(DEFAULTS)
  const [isDirty, setIsDirty] = useState(false)
  const [smtpResult, setSmtpResult] = useState(null)
  const [dna, setDna] = useState('')
  const [dnaDirty, setDnaDirty] = useState(false)
  const [modal, setModal] = useState(null) // 'clear-leads' | 'reset-stats'
  const dnaOrigRef = useRef('')

  // ── Load settings from DB ──
  const { data: stored } = useQuery({
    queryKey: ['settings'],
    queryFn: settingsApi.getAll,
  })

  useEffect(() => {
    if (stored) {
      setValues((v) => ({ ...v, ...stored }))
      setIsDirty(false)
    }
  }, [stored])

  // ── Load Company DNA ──
  const { data: dnaData } = useQuery({
    queryKey: ['settings-dna'],
    queryFn: settingsApi.getDna,
  })

  useEffect(() => {
    if (dnaData && dnaOrigRef.current === '') {
      const content = dnaData.content || ''
      setDna(content)
      dnaOrigRef.current = content
    }
  }, [dnaData])

  // ── Ollama live status — poll every 10s ──
  const { data: ollamaStatus, isFetching: ollamaChecking } = useQuery({
    queryKey: ['ollama-status'],
    queryFn: aiApi.status,
    refetchInterval: 10_000,
    retry: false,
  })

  const set = (key, val) => {
    setValues((v) => ({ ...v, [key]: val }))
    setIsDirty(true)
  }

  // ── Mutations ──
  const saveMut = useMutation({
    mutationFn: () => settingsApi.bulkUpdate(values),
    onSuccess: () => {
      toast.success('Settings saved')
      setIsDirty(false)
      qc.invalidateQueries({ queryKey: ['settings'] })
    },
    onError: (e) => toast.error(e.message),
  })

  // Saves SMTP fields to DB first so backend test reads the fresh values
  const testSmtpMut = useMutation({
    mutationFn: async () => {
      const smtp = {
        smtp_host: values.smtp_host,
        smtp_port: values.smtp_port,
        smtp_username: values.smtp_username,
        smtp_password: values.smtp_password,
        smtp_from_name: values.smtp_from_name,
        smtp_from_email: values.smtp_from_email,
      }
      await settingsApi.bulkUpdate(smtp)
      return settingsApi.testSmtp()
    },
    onSuccess: (d) => setSmtpResult(d),
    onError: (e) => setSmtpResult({ success: false, error: e.message }),
  })

  const saveDnaMut = useMutation({
    mutationFn: () => settingsApi.saveDna(dna),
    onSuccess: () => {
      toast.success('Company DNA saved')
      setDnaDirty(false)
      dnaOrigRef.current = dna
    },
    onError: (e) => toast.error(e.message),
  })

  const clearLeadsMut = useMutation({
    mutationFn: () => leadsApi.deleteAll(),
    onSuccess: (d) => {
      toast.success(`Deleted ${d.deleted} leads`)
      setModal(null)
      qc.invalidateQueries({ queryKey: ['leads'] })
      qc.invalidateQueries({ queryKey: ['stats'] })
    },
    onError: (e) => { toast.error(e.message); setModal(null) },
  })

  const resetStatsMut = useMutation({
    mutationFn: () => settingsApi.resetStats(),
    onSuccess: () => {
      toast.success('Campaign stats cleared')
      setModal(null)
      qc.invalidateQueries({ queryKey: ['stats'] })
      qc.invalidateQueries({ queryKey: ['campaign-history'] })
    },
    onError: (e) => { toast.error(e.message); setModal(null) },
  })

  const ollamaConnected = ollamaStatus?.connected
  const ollamaModel = ollamaStatus?.model || values.ollama_model

  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-4xl">
      {/* ─── Header ─── */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-slate-100">Settings</h1>
          <p className="text-sm text-slate-500 mt-0.5">Configure your marketing engine</p>
        </div>
        <button
          onClick={() => saveMut.mutate()}
          disabled={saveMut.isPending || !isDirty}
          className={clsx('btn-primary', !isDirty && 'opacity-50 cursor-not-allowed')}
        >
          {saveMut.isPending
            ? <><RefreshCw size={14} className="animate-spin" /> Saving…</>
            : <><Save size={14} /> Save All</>}
        </button>
      </div>

      {/* ─── Email SMTP ─── */}
      <SectionCard
        title="Email (SMTP)"
        description="Gmail requires an App Password — not your regular password."
        icon={Mail}
        iconColor="text-blue-400"
      >
        <div className="grid grid-cols-2 gap-3">
          <Field label="SMTP Host" name="smtp_host" value={values.smtp_host} onChange={set} placeholder="smtp.gmail.com" />
          <Field label="Port" name="smtp_port" value={values.smtp_port} onChange={set} placeholder="587" />
        </div>
        <Field
          label="Username / Email"
          name="smtp_username"
          value={values.smtp_username}
          onChange={set}
          placeholder="you@gmail.com"
        />
        <Field
          label="Password / App Password"
          name="smtp_password"
          type="password"
          value={values.smtp_password}
          onChange={set}
          hint="Gmail: Settings → Security → 2-Step Verification → App Passwords → Mail"
        />
        <div className="grid grid-cols-2 gap-3">
          <Field label="From Name" name="smtp_from_name" value={values.smtp_from_name} onChange={set} placeholder="Your Name" />
          <Field label="From Email" name="smtp_from_email" value={values.smtp_from_email} onChange={set} placeholder="you@gmail.com" />
        </div>

        {smtpResult && (
          <div className={clsx(
            'flex items-start gap-2.5 p-3 rounded-lg text-sm border',
            smtpResult.success
              ? 'bg-emerald-900/30 border-emerald-700/50 text-emerald-300'
              : 'bg-red-900/30 border-red-700/50 text-red-300'
          )}>
            {smtpResult.success
              ? <CheckCircle2 size={15} className="mt-0.5 shrink-0" />
              : <XCircle size={15} className="mt-0.5 shrink-0" />}
            <span>
              {smtpResult.success
                ? `Connected — sending as ${smtpResult.from_email || values.smtp_from_email}`
                : `Failed: ${smtpResult.error}`}
            </span>
          </div>
        )}

        <button
          onClick={() => { setSmtpResult(null); testSmtpMut.mutate() }}
          disabled={testSmtpMut.isPending}
          className="btn-secondary text-xs w-full justify-center"
        >
          {testSmtpMut.isPending
            ? <><RefreshCw size={13} className="animate-spin" /> Testing…</>
            : 'Test SMTP Connection'}
        </button>
      </SectionCard>

      {/* ─── Email Senders (campaign Send From) ─── */}
      <EmailSendersSection />

      {/* ─── IMAP / Reply Detection ─── */}
      <SectionCard
        title="IMAP — Reply Detection"
        description="Auto-detects email replies and marks leads as REPLIED. Uses the same Gmail account."
        icon={Inbox}
        iconColor="text-emerald-400"
      >
        <div className="grid grid-cols-2 gap-3">
          <Field label="IMAP Host" name="imap_host" value={values.imap_host} onChange={set} placeholder="imap.gmail.com" />
          <Field label="Port" name="imap_port" value={values.imap_port} onChange={set} placeholder="993" />
        </div>
        <Field
          label="Username / Email"
          name="imap_username"
          value={values.imap_username}
          onChange={set}
          placeholder="you@gmail.com"
          hint="Usually the same as your SMTP username."
        />
        <Field
          label="Password / App Password"
          name="imap_password"
          type="password"
          value={values.imap_password}
          onChange={set}
        />
        <Toggle
          label="Use SSL (recommended)"
          description="IMAPS on port 993 — disable only for non-SSL IMAP on port 143."
          checked={values.imap_ssl === 'true'}
          onChange={() => set('imap_ssl', values.imap_ssl === 'true' ? 'false' : 'true')}
        />
      </SectionCard>

      {/* ─── Ollama AI ─── */}
      {!isClientEdition() && (
      <SectionCard
        title="Ollama AI"
        description="Local LLM — zero cost, fully private. Run: ollama serve"
        icon={Bot}
        iconColor="text-purple-400"
        actions={
          <div className={clsx(
            'flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full font-medium border',
            ollamaConnected
              ? 'bg-emerald-900/40 text-emerald-400 border-emerald-700/50'
              : 'bg-slate-800 text-slate-500 border-slate-700'
          )}>
            <span className={clsx(
              'w-1.5 h-1.5 rounded-full',
              ollamaConnected ? 'bg-emerald-400 animate-pulse' : 'bg-slate-500'
            )} />
            {ollamaChecking ? 'Checking…' : ollamaConnected ? `${ollamaModel}` : 'Offline'}
          </div>
        }
      >
        <Field
          label="Ollama URL"
          name="ollama_base_url"
          value={values.ollama_base_url}
          onChange={set}
          hint="Default: http://localhost:11434"
        />
        <Field label="Model" name="ollama_model" value={values.ollama_model} onChange={set} placeholder="llama3" />
        <Field
          label="Timeout"
          name="ollama_timeout"
          value={values.ollama_timeout}
          onChange={set}
          placeholder="120"
          suffix="sec"
        />
        {!ollamaConnected && !ollamaChecking && (
          <div className="flex items-start gap-2 text-xs text-amber-300 bg-amber-900/20 border border-amber-700/40 rounded-lg p-3">
            <AlertTriangle size={13} className="mt-0.5 shrink-0" />
            <span>
              Ollama is offline. Open a terminal and run:{' '}
              <code className="font-mono bg-slate-800 px-1.5 py-0.5 rounded">ollama serve</code>
            </span>
          </div>
        )}
      </SectionCard>
      )}

      {/* ─── Cloud LLM (optional) ─── */}
      {!isClientEdition() && (
      <SectionCard
        title="Cloud LLM (Optional)"
        description="Ollama above stays the default and keeps AutoLead fully offline. Only switch this if you want higher-quality generation and don't mind API costs."
        icon={Globe}
        iconColor="text-sky-400"
      >
        <div>
          <label className="label">Provider</label>
          <div className="flex gap-2">
            {[
              { id: 'ollama',    label: 'Ollama (local)' },
              { id: 'openai',    label: 'OpenAI' },
              { id: 'anthropic', label: 'Anthropic' },
            ].map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => set('llm_provider', p.id)}
                className={clsx(
                  'flex-1 py-2 rounded-lg text-xs font-semibold border transition-colors',
                  values.llm_provider === p.id
                    ? 'bg-brand-600/20 border-brand-500/60 text-brand-300'
                    : 'bg-slate-800/50 border-slate-700/60 text-slate-400 hover:border-slate-600'
                )}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>

        {values.llm_provider === 'openai' && (
          <>
            <Field
              label="OpenAI API Key" name="openai_api_key" type="password"
              value={values.openai_api_key} onChange={set} placeholder="sk-…"
            />
            <Field
              label="Model" name="openai_model"
              value={values.openai_model} onChange={set} placeholder="gpt-4o-mini"
            />
          </>
        )}

        {values.llm_provider === 'anthropic' && (
          <>
            <Field
              label="Anthropic API Key" name="anthropic_api_key" type="password"
              value={values.anthropic_api_key} onChange={set} placeholder="sk-ant-…"
            />
            <Field
              label="Model" name="anthropic_model"
              value={values.anthropic_model} onChange={set} placeholder="claude-3-5-haiku-20241022"
            />
          </>
        )}

        {values.llm_provider !== 'ollama' && (
          <p className="text-xs text-slate-500">
            If no API key is set for the selected provider, generation automatically falls back to Ollama.
          </p>
        )}
      </SectionCard>
      )}

      {/* ─── Engine & Limits ─── */}
      <SectionCard
        title="Engine & Limits"
        description="Daily schedule and send rate limits."
        icon={Zap}
        iconColor="text-amber-400"
      >
        <div className="grid grid-cols-2 gap-3">
          <Field
            label="Daily Email Limit"
            name="daily_email_limit"
            value={values.daily_email_limit}
            onChange={set}
            placeholder="50"
            suffix="/ day"
          />
          <Field
            label="Daily WhatsApp Limit"
            name="daily_whatsapp_limit"
            value={values.daily_whatsapp_limit}
            onChange={set}
            placeholder="20"
            suffix="/ day"
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field
            label="Schedule Hour (UTC)"
            name="schedule_hour"
            value={values.schedule_hour}
            onChange={set}
            placeholder="9"
            hint="0–23, UTC"
          />
          <Field
            label="Follow-up Delay"
            name="followup_delay_days"
            value={values.followup_delay_days}
            onChange={set}
            placeholder="3"
            suffix="days"
          />
        </div>
        <Toggle
          label="Auto-send enabled"
          description="When on, the scheduler sends outreach automatically every day without manual intervention."
          checked={values.auto_send_enabled === 'true'}
          onChange={() => set('auto_send_enabled', values.auto_send_enabled === 'true' ? 'false' : 'true')}
        />
      </SectionCard>

      {/* ─── WhatsApp ─── */}
      {!isClientEdition() && (
      <SectionCard
        title="WhatsApp"
        description="Browser automation via pywhatkit — requires an active WhatsApp Web session."
        icon={MessageCircle}
        iconColor="text-green-400"
      >
        <Field
          label="Wait Time"
          name="whatsapp_wait_time"
          value={values.whatsapp_wait_time}
          onChange={set}
          hint="Seconds to wait for WhatsApp Web to load before sending"
          suffix="sec"
        />
        <Toggle
          label="Auto-close browser tab after sending"
          checked={values.whatsapp_close_tab === 'true'}
          onChange={() => set('whatsapp_close_tab', values.whatsapp_close_tab === 'true' ? 'false' : 'true')}
        />
      </SectionCard>
      )}

      {/* ─── Scraper ─── */}
      <SectionCard
        title="Scraper"
        description="Google Maps lead scraping via Playwright."
        icon={Globe}
        iconColor="text-sky-400"
      >
        <Toggle
          label="Run browser headless (invisible)"
          description="Disable to watch the browser scrape in real time — useful for debugging."
          checked={values.scraper_headless === 'true'}
          onChange={() => set('scraper_headless', values.scraper_headless === 'true' ? 'false' : 'true')}
        />
        <div className="grid grid-cols-2 gap-3">
          <Field
            label="Min Delay"
            name="scraper_delay_min"
            value={values.scraper_delay_min}
            onChange={set}
            placeholder="2.0"
            suffix="sec"
          />
          <Field
            label="Max Delay"
            name="scraper_delay_max"
            value={values.scraper_delay_max}
            onChange={set}
            placeholder="5.0"
            suffix="sec"
          />
        </div>
      </SectionCard>

      {/* ─── Lead Search & Research ─── */}
      <SectionCard
        title="Lead Search & Research"
        description="Post-discovery enrichment and how leads reach the Research Agent."
        icon={Bot}
        iconColor="text-indigo-400"
      >
        <Toggle
          label="Enrich & score new leads automatically"
          description="After every search (manual or automation), find missing emails, analyse websites, and assign a HOT/WARM/COLD score."
          checked={values.discovery_enrichment_enabled !== 'false'}
          onChange={() => set('discovery_enrichment_enabled', values.discovery_enrichment_enabled === 'false' ? 'true' : 'false')}
        />
        <Toggle
          label="Automatic Research Agent handoff"
          description="When on, qualified new leads are sent to the Research Agent automatically. When off, you send them yourself from the Leads list. This setting persists until you turn it off."
          checked={values.research_handoff_mode === 'automatic'}
          onChange={() => set('research_handoff_mode', values.research_handoff_mode === 'automatic' ? 'manual' : 'automatic')}
        />
        <Field
          label="Minimum score for automatic handoff"
          name="research_handoff_min_score"
          value={values.research_handoff_min_score}
          onChange={set}
          placeholder="60"
          hint="0–100 · only leads at or above this score are auto-sent"
        />
      </SectionCard>

      {!isClientEdition() && <SearchProvidersSection />}

      {/* ─── Company DNA ─── */}
      <SectionCard
        title="Company DNA"
        description="Your company profile. The AI reads it before every search, research and message it writes."
        icon={FileText}
        iconColor="text-rose-400"
      >
        <CompanyDnaEditor
          value={dna}
          onChange={(v) => { setDna(v); setDnaDirty(v !== dnaOrigRef.current) }}
          dirty={dnaDirty}
          saving={saveDnaMut.isPending}
          onSave={() => saveDnaMut.mutate()}
        />
      </SectionCard>

      <AppLockCard />

      {/* ─── Danger Zone ─── */}
      <SectionCard
        title="Danger Zone"
        description="Irreversible operations — these cannot be undone."
        icon={Shield}
        iconColor="text-red-400"
      >
        <div className="space-y-3">
          <div className="flex items-center justify-between p-3.5 rounded-lg border border-slate-700/60 bg-slate-800/40">
            <div>
              <p className="text-sm font-medium text-slate-300">Clear All Leads</p>
              <p className="text-xs text-slate-500 mt-0.5">Permanently delete every lead from the database.</p>
            </div>
            <button
              onClick={() => setModal('clear-leads')}
              className="btn text-xs border-red-800/60 text-red-400 hover:bg-red-900/30 hover:border-red-600 shrink-0 ml-4"
            >
              <Trash2 size={13} /> Clear Leads
            </button>
          </div>

          <div className="flex items-center justify-between p-3.5 rounded-lg border border-slate-700/60 bg-slate-800/40">
            <div>
              <p className="text-sm font-medium text-slate-300">Reset Campaign Stats</p>
              <p className="text-xs text-slate-500 mt-0.5">Delete all campaign logs and run history.</p>
            </div>
            <button
              onClick={() => setModal('reset-stats')}
              className="btn text-xs border-red-800/60 text-red-400 hover:bg-red-900/30 hover:border-red-600 shrink-0 ml-4"
            >
              <RotateCcw size={13} /> Reset Stats
            </button>
          </div>
        </div>
      </SectionCard>

      {/* ─── Confirmation modal ─── */}
      {modal && (
        <ConfirmModal
          action={modal}
          onCancel={() => setModal(null)}
          onConfirm={() => {
            if (modal === 'clear-leads') clearLeadsMut.mutate()
            else resetStatsMut.mutate()
          }}
        />
      )}
    </div>
  )
}
