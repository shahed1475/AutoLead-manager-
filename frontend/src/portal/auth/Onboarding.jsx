import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, ArrowRight, Check, LogOut, RefreshCw } from 'lucide-react'
import clsx from 'clsx'
import CompanyDnaEditor, { countWords } from '../../components/settings/CompanyDnaEditor'
import { portalApi } from '../api'
import { usePortalInfo } from '../hooks'
import { Brand, ThemeSwitch } from '../site/SiteLayout'
import { FormError } from './fields'

// First-run set-up, between sign-up and the dashboard: who you are, your
// sector, and your Company DNA (the profile the AI uses for every search,
// research and message). Saved to the account; the workspace gets a copy.

const SECTORS = [
  'Marketing & advertising', 'Web design & development', 'IT services & software', 'Consulting',
  'Accounting & finance', 'Real estate', 'Healthcare & clinics', 'Education & training',
  'Construction & trades', 'Hospitality & restaurants', 'Retail & e-commerce', 'Manufacturing',
  'Logistics & transport', 'Legal services', 'Recruitment & HR',
]
const STEPS = ['About you', 'Your sector', 'Company DNA']
const MIN_WORDS = 20

function starterDna(company, sector) {
  const who = company ? `We are ${company}${sector ? `, a ${sector.toLowerCase()} company` : ''}.` : ''
  return `## Who we are\n${who}\n\n## What we sell\n\n\n## Ideal customers\n\n\n## Problems we solve\n\n\n## Why choose us\n`
}

const ownWords = countWords       // the starter's headings don't count

export default function Onboarding({ me, onSignOut }) {
  const qc = useQueryClient()
  const { name: product } = usePortalInfo()
  const draftKey = `hom-onboarding:${me.email}`
  const [data, setData] = useState(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(draftKey) || 'null')
      if (saved) return saved
    } catch { /* private mode */ }
    return { step: 0, name: me.name || '', company: me.company || '', sector: me.sector || '', otherSector: '', dna: '' }
  })
  useEffect(() => {
    try { localStorage.setItem(draftKey, JSON.stringify(data)) } catch { /* private mode */ }
  }, [data, draftKey])
  const set = (patch) => setData((d) => ({ ...d, ...patch }))
  const sector = data.sector === 'Other' ? data.otherSector.trim() : data.sector

  const finish = useMutation({
    mutationFn: () => portalApi.onboarding({ name: data.name, company: data.company, sector, company_dna: data.dna }),
    onSuccess: (client) => {
      try { localStorage.removeItem(draftKey) } catch { /* private mode */ }
      qc.setQueryData(['portal-me'], client)
    },
  })

  async function next() {
    if (data.step === 1 && !data.dna.trim()) {
      // A ready-made draft for known sectors; the blank starter otherwise.
      let dna = null
      try { dna = (await portalApi.sectorDna(sector, data.company)).dna } catch { /* offline: blank starter */ }
      set({ step: 2, dna: dna || starterDna(data.company, sector) })
    } else set({ step: data.step + 1 })
    window.scrollTo(0, 0)
  }
  const canContinue = [data.name.trim(), sector, ownWords(data.dna) >= MIN_WORDS][data.step]

  return (
    <div className="min-h-[100dvh] bg-background">
      <header className="pt-safe border-b border-border-subtle">
        <div className="max-w-3xl mx-auto h-16 px-4 sm:px-6 flex items-center gap-3">
          <Brand />
          <div className="flex-1" />
          <ThemeSwitch />
          <button type="button" onClick={onSignOut} className="btn-ghost h-9 px-3 text-sm"><LogOut size={15} /> <span className="hidden sm:inline">Sign out</span></button>
        </div>
      </header>

      <main id="main" className="max-w-3xl mx-auto px-4 sm:px-6 py-10 sm:py-14 pb-safe">
        <p className="text-sm font-semibold text-primary">Set up your workspace</p>
        <ol className="mt-4 grid grid-cols-3 gap-2" aria-label="Progress">
          {STEPS.map((label, i) => (
            <li key={label} aria-current={i === data.step ? 'step' : undefined}>
              <span className={clsx('block h-1 rounded-full transition-colors', i <= data.step ? 'bg-primary' : 'bg-secondary')} />
              <span className={clsx('mt-2 flex items-center gap-1.5 text-xs font-medium',
                i === data.step ? 'text-foreground' : 'text-muted-foreground')}>
                {i < data.step && <Check size={12} className="text-primary" />}
                <span className="hidden sm:inline">Step {i + 1} ·</span> {label}
              </span>
            </li>
          ))}
        </ol>

        <form className="mt-10 animate-page-in" key={data.step}
          onSubmit={(e) => { e.preventDefault(); if (!canContinue) return; if (data.step < 2) next(); else finish.mutate() }}>
          {data.step === 0 && (
            <section className="max-w-md space-y-5">
              <div>
                <h1 className="text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-foreground">Welcome to {product}</h1>
                <p className="text-support mt-2">A few quick questions so {product} can find the right leads and write in your voice. It takes about two minutes.</p>
              </div>
              <div>
                <label htmlFor="ob-name" className="label">Your name</label>
                <input id="ob-name" className="input h-11" autoComplete="name" autoFocus required value={data.name} onChange={(e) => set({ name: e.target.value })} />
              </div>
              <div>
                <label htmlFor="ob-company" className="label">Company name <span className="font-normal">(optional)</span></label>
                <input id="ob-company" className="input h-11" autoComplete="organization" value={data.company} onChange={(e) => set({ company: e.target.value })} />
              </div>
            </section>
          )}

          {data.step === 1 && (
            <section className="space-y-5">
              <div className="max-w-xl">
                <h1 className="text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-foreground">Which sector are you in?</h1>
                <p className="text-support mt-2">Your own industry — it helps {product} understand what you offer.</p>
              </div>
              <div className="grid gap-2 grid-cols-1 sm:grid-cols-2 lg:grid-cols-3" role="radiogroup" aria-label="Sector">
                {[...SECTORS, 'Other'].map((s) => (
                  <label key={s} className={clsx('flex items-center gap-2.5 cursor-pointer rounded-xl border px-3.5 py-3 text-sm transition-colors',
                    data.sector === s ? 'border-primary bg-primary/5 text-foreground font-medium' : 'border-border text-foreground/85 hover:bg-secondary')}>
                    <input type="radio" name="sector" value={s} className="sr-only" checked={data.sector === s} onChange={() => set({ sector: s })} />
                    <span className={clsx('grid place-items-center size-4 rounded-full border shrink-0', data.sector === s ? 'border-primary bg-primary' : 'border-border')}>
                      {data.sector === s && <span className="size-1.5 rounded-full bg-primary-foreground" />}
                    </span>
                    {s === 'Other' ? 'Something else' : s}
                  </label>
                ))}
              </div>
              {data.sector === 'Other' && (
                <div className="max-w-md">
                  <label htmlFor="ob-other" className="label">Your sector</label>
                  <input id="ob-other" className="input h-11" autoFocus maxLength={80} placeholder="e.g. Solar installation"
                    value={data.otherSector} onChange={(e) => set({ otherSector: e.target.value })} />
                </div>
              )}
            </section>
          )}

          {data.step === 2 && (
            <section className="space-y-5">
              <div className="max-w-2xl">
                <h1 className="text-2xl sm:text-[1.75rem] font-semibold tracking-tight text-foreground">Your Company DNA</h1>
                <p className="text-support mt-2">Describe your company in your own words. {product} reads this before every search, research and message — the clearer it is, the better your leads and drafts. You can change it any time in Settings.</p>
                <p className="text-support mt-2">Add what makes you different under “Why choose us”. The AI never makes that part up.</p>
              </div>
              <FormError message={finish.error?.message} />
              <CompanyDnaEditor value={data.dna} onChange={(v) => set({ dna: v })} minHeight={320}
                actions={<span className="text-xs text-muted-foreground">{ownWords(data.dna) < MIN_WORDS ? `At least ${MIN_WORDS} words to finish.` : 'Ready — you can finish now.'}</span>} />
            </section>
          )}

          <div className="mt-10 pt-6 border-t border-border-subtle flex items-center justify-between gap-3">
            {data.step > 0
              ? <button type="button" className="btn-ghost h-11" onClick={() => set({ step: data.step - 1 })}><ArrowLeft size={15} /> Back</button>
              : <span />}
            <button type="submit" className="btn-primary h-11 px-6" disabled={!canContinue || finish.isPending}>
              {finish.isPending && <RefreshCw size={14} className="animate-spin" />}
              {data.step < 2 ? <>Continue <ArrowRight size={15} /></> : 'Finish and open my dashboard'}
            </button>
          </div>
        </form>
      </main>
    </div>
  )
}
