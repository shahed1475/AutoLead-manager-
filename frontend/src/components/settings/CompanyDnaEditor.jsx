import { useEffect, useLayoutEffect, useRef } from 'react'
import { Check, Plus, RefreshCw, Save } from 'lucide-react'
import clsx from 'clsx'

// Company DNA — the company profile the AI reads before every search,
// research and message. A roomy editor with a guided outline, so it's easy to
// write a complete profile (and to see which parts are still missing).

const OUTLINE = [
  ['Who we are', 'Company name, what you do, where you are based.'],
  ['What we sell', 'Your main services or products, and how they are delivered.'],
  ['Ideal customers', 'Industries, company size, locations and the roles you sell to.'],
  ['Problems we solve', 'The pains your customers have before they work with you.'],
  ['Why choose us', 'What makes you different from the alternatives.'],
  ['Proof & results', 'Clients, case studies, numbers you can stand behind.'],
  ['Tone of voice', 'How your messages should sound: formal, friendly, direct…'],
]

const PLACEHOLDER = `## Who we are
We are [Company], a [type of business] based in [city].

## What we sell
[Main services or products]

## Ideal customers
[Industries, company size, locations, the roles you sell to]

## Problems we solve
[What your customers struggle with before they work with you]

## Why choose us
[What makes you different]

## Tone of voice
[Professional / friendly / direct]`

function lengthHint(words) {
  if (words === 0) return { text: 'Start with the outline above — aim for 150–600 words.', tone: 'muted' }
  if (words < 80) return { text: 'A bit short — more detail helps the AI find and write to the right people.', tone: 'warning' }
  if (words <= 900) return { text: 'Good length.', tone: 'success' }
  return { text: 'Very long — keep it focused so the AI uses the important parts.', tone: 'warning' }
}

export default function CompanyDnaEditor({ value, onChange, dirty, saving, onSave }) {
  const ref = useRef(null)
  const words = value.trim() ? value.trim().split(/\s+/).length : 0
  const hint = lengthHint(words)
  const has = (heading) => new RegExp(`^#+\\s*${heading.replace(/[.*+?^${}()|[\]\\&]/g, '\\$&')}\\s*$`, 'im').test(value)

  // Grow with the text (comfortably tall to start, scrolls past ~70% of the screen).
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(Math.max(el.scrollHeight + 2, 384), window.innerHeight * 0.7)}px`
  }, [value])

  // Ctrl/Cmd + S saves while editing.
  useEffect(() => {
    const onKey = (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's' && document.activeElement === ref.current) {
        e.preventDefault()
        if (dirty && !saving) onSave()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [dirty, saving, onSave])

  function addSection(heading) {
    const el = ref.current
    const base = value.replace(/\s+$/, '')
    const next = `${base}${base ? '\n\n' : ''}## ${heading}\n`
    onChange(next)
    requestAnimationFrame(() => {
      if (!el) return
      el.focus()
      el.setSelectionRange(next.length, next.length)
      el.scrollTop = el.scrollHeight
    })
  }

  return (
    <div className="space-y-4">
      <div>
        <p className="text-xs font-semibold text-muted-foreground mb-2">A complete profile covers</p>
        <div className="flex flex-wrap gap-1.5">
          {OUTLINE.map(([heading, tip]) => {
            const done = has(heading)
            return (
              <button key={heading} type="button" title={done ? `${heading} — covered` : tip}
                onClick={() => !done && addSection(heading)}
                aria-label={done ? `${heading} (covered)` : `Add section: ${heading}`}
                className={clsx('inline-flex items-center gap-1.5 h-8 px-3 rounded-full border text-[13px] font-medium transition-colors',
                  done ? 'border-success/30 bg-success/10 text-success cursor-default'
                    : 'border-border text-muted-foreground hover:text-foreground hover:border-primary/50 hover:bg-primary/5')}>
                {done ? <Check size={12} strokeWidth={2.5} /> : <Plus size={12} strokeWidth={2.2} />}
                {heading}
              </button>
            )
          })}
        </div>
      </div>

      <textarea
        ref={ref}
        aria-label="Company DNA"
        className="input w-full min-h-96 resize-y px-4 py-3.5 text-sm leading-7 font-sans"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={PLACEHOLDER}
        spellCheck
      />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs flex items-center gap-2">
          <span className="tabular-nums text-foreground font-medium">{words} words</span>
          <span className={clsx(hint.tone === 'success' && 'text-success', hint.tone === 'warning' && 'text-warning',
            hint.tone === 'muted' && 'text-muted-foreground')}>{hint.text}</span>
        </p>
        <div className="flex items-center gap-3">
          {dirty && <span className="text-xs text-muted-foreground flex items-center gap-1.5"><span className="size-1.5 rounded-full bg-warning" />Unsaved changes</span>}
          <button type="button" onClick={onSave} disabled={saving || !dirty} className="btn-primary h-9 px-4 text-sm">
            {saving ? <><RefreshCw size={14} className="animate-spin" /> Saving…</> : <><Save size={14} /> Save</>}
          </button>
        </div>
      </div>
    </div>
  )
}
