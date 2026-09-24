import { Link } from 'react-router-dom'
import {
  ArrowRight, BadgeCheck, CalendarClock, Check, ChevronDown, FileSearch, GitBranch, KeyRound, Lock, MailCheck,
  PenLine, Search, ShieldCheck, Sparkles, Target, Users,
} from 'lucide-react'
import clsx from 'clsx'
import { usePortalInfo } from '../hooks'

// The public HOM website (the client link's front page). Every claim here
// describes something the product actually does.

const STEPS = [
  ['Tell HOM who you want', 'Pick a type of business, a location and how many leads you need. That’s the whole brief.', Target],
  ['HOM finds and researches', 'It searches maps listings, business directories and search engines, removes duplicates, then reads each company’s website to find the people who decide.', FileSearch],
  ['You review and reach out', 'Personal drafts are ready for every lead. Edit, approve and send from your own email, then follow the replies in your pipeline.', MailCheck],
]

const FEATURES = [
  ['Lead discovery', 'Several sources searched at once and merged into one clean list — duplicates removed, sources kept.', Search],
  ['Decision-maker research', 'An AI research agent reads company websites to find owners, founders and managers, with their job titles.', Users],
  ['Evidence, never guesses', 'Each contact detail comes with where it was found. If something can’t be found, it’s marked as not found — never made up.', BadgeCheck],
  ['Outreach you approve', 'Messages are drafted from your company profile, one lead at a time. Nothing is sent until you approve it.', PenLine],
  ['Pipeline and replies', 'Move leads from first contact to won, and see replies sorted by intent so you know who to answer first.', GitBranch],
  ['Daily automation', 'Schedule searches to run every day so new, researched leads keep arriving while you work.', CalendarClock],
]

const ALSO = ['Hot, warm and cold lead scores', 'Email campaigns from your own account', 'CSV export of your leads',
  'Opt-outs respected automatically', 'Works on desktop and phone', 'Light and dark mode']

const FAQ = [
  ['What is HOM?', 'HOM is a sales growth engine for finding new clients. It finds businesses that match who you sell to, researches them to identify decision makers, and helps you write and send personal outreach.'],
  ['Do I need any technical skills?', 'No. You describe the businesses you want — type, location and how many — and HOM does the searching and research. Everything happens in your browser.'],
  ['Who sends the emails?', 'You do, from your own email account, which you connect in Settings. HOM drafts the messages; you review and approve them before they go out.'],
  ['Is my data private?', 'Yes. Every account gets its own private workspace with its own database. Other accounts can’t see your leads, messages or settings.'],
  ['How accurate is the contact data?', 'HOM only saves details it actually found, with the source. When it can’t confirm something, the field says so instead of guessing.'],
  ['What does it cost?', 'Early access is free and needs no card. Plans will be announced before any pricing applies, and you’ll decide then whether to continue.'],
]

const PREVIEW_ROWS = [
  ['Pearl Dental Clinic', 'Dr. Amira Hassan', 'Clinic Director', 'HOT'],
  ['Smile Studio Dubai', 'Omar Khalid', 'Founder', 'HOT'],
  ['Bright Dental Care', 'Sara Malik', 'Practice Manager', 'WARM'],
  ['City Dental Center', 'Not found', '', 'WARM'],
  ['Marina Smile Clinic', 'Dr. Leila Noor', 'Owner', 'COLD'],
]
const SCORE = { HOT: 'bg-error/10 text-error', WARM: 'bg-warning/10 text-warning', COLD: 'bg-info/10 text-info' }

function ProductPreview() {
  return (
    <div className="relative" aria-label="Example of the HOM leads screen" role="img">
      <div className="absolute -inset-6 sm:-inset-10 rounded-[2.5rem] bg-primary/10 blur-3xl" aria-hidden="true" />
      <div className="relative rounded-2xl border border-border bg-surface shadow-2xl overflow-hidden">
        <div className="flex items-center gap-1.5 px-4 h-10 border-b border-border-subtle bg-surface-elevated">
          <span className="size-2.5 rounded-full bg-error/60" /><span className="size-2.5 rounded-full bg-warning/60" /><span className="size-2.5 rounded-full bg-success/60" />
          <span className="ml-3 text-2xs text-muted-foreground">Leads · Dental clinics in Dubai</span>
        </div>
        <div className="p-4 sm:p-5">
          <div className="flex flex-wrap items-center gap-2 mb-4">
            <span className="text-sm font-semibold text-foreground">24 leads found</span>
            <span className="text-2xs rounded-full bg-success/10 text-success px-2 py-0.5 font-semibold">Research complete</span>
            <span className="ml-auto text-2xs text-muted-foreground">Example data</span>
          </div>
          <ul className="divide-y divide-border-subtle text-[13px]">
            {PREVIEW_ROWS.map(([biz, person, title, score]) => (
              <li key={biz} className="grid grid-cols-[1.2fr_1.2fr_auto] items-center gap-3 py-2.5">
                <span className="font-medium text-foreground truncate">{biz}</span>
                <span className={clsx('truncate', person === 'Not found' ? 'text-muted-foreground italic' : 'text-foreground/80')}>
                  {person}{title && <span className="text-muted-foreground"> · {title}</span>}
                </span>
                <span className={clsx('text-2xs font-semibold rounded-md px-2 py-0.5', SCORE[score])}>{score}</span>
              </li>
            ))}
          </ul>
          <div className="mt-4 rounded-xl border border-border-subtle bg-background/60 p-3.5">
            <p className="text-2xs font-semibold uppercase tracking-wider text-muted-foreground">Draft · waiting for your approval</p>
            <p className="mt-1.5 text-[13px] text-foreground/85 leading-relaxed">Hi Dr. Hassan — I noticed Pearl Dental Clinic doesn’t offer online booking yet…</p>
          </div>
        </div>
      </div>
    </div>
  )
}

function SectionHead({ eyebrow, title, text, center = true }) {
  return (
    <div className={clsx('max-w-2xl', center && 'mx-auto text-center')}>
      <p className="text-sm font-semibold text-primary">{eyebrow}</p>
      <h2 className="mt-2 text-3xl sm:text-4xl font-semibold tracking-tight text-foreground text-balance">{title}</h2>
      {text && <p className="mt-4 text-lg text-muted-foreground leading-relaxed text-balance">{text}</p>}
    </div>
  )
}

export default function Landing() {
  const { name, welcome } = usePortalInfo()
  return (
    <>
      {/* Hero */}
      <section className="relative overflow-hidden">
        <div className="absolute inset-x-0 top-0 h-[36rem] bg-gradient-to-b from-primary/[0.07] to-transparent" aria-hidden="true" />
        <div className="relative max-w-6xl mx-auto px-4 sm:px-6 pt-12 sm:pt-20 pb-16 sm:pb-24 grid gap-14 lg:grid-cols-[1.05fr_1fr] lg:items-center">
          <div>
            <a href="#early-access" className="inline-flex items-center gap-2 rounded-full border border-border bg-surface px-3 py-1 text-xs font-medium text-foreground hover:border-primary/50">
              <span className="size-1.5 rounded-full bg-primary" /> Early access is open <ArrowRight size={12} />
            </a>
            <h1 className="mt-6 text-4xl sm:text-5xl lg:text-[3.5rem] font-semibold tracking-tight leading-[1.08] text-foreground text-balance">
              Find the right leads. Reach the people who decide.
            </h1>
            <p className="mt-6 text-lg sm:text-xl text-muted-foreground leading-relaxed max-w-xl text-balance">
              {name} searches your market, researches every business to find its decision makers, and drafts personal outreach you approve — all in one private workspace.
            </p>
            {welcome && <p className="mt-4 text-base text-foreground/85 max-w-xl whitespace-pre-line">{welcome}</p>}
            <div className="mt-8 flex flex-col sm:flex-row gap-3">
              <Link to="/signup" className="btn-primary h-12 px-6 text-base">Get started free <ArrowRight size={16} /></Link>
              <a href="#how-it-works" className="btn-secondary h-12 px-6 text-base">See how it works</a>
            </div>
            <ul className="mt-6 flex flex-wrap gap-x-5 gap-y-2 text-sm text-muted-foreground">
              {['No card needed', 'Private workspace', 'You approve every message'].map((t) => (
                <li key={t} className="flex items-center gap-1.5"><Check size={15} className="text-primary" />{t}</li>
              ))}
            </ul>
          </div>
          <ProductPreview />
        </div>
      </section>

      {/* How it works */}
      <section id="how-it-works" className="scroll-mt-20 py-20 sm:py-28 bg-surface/60 border-y border-border-subtle">
        <div className="max-w-6xl mx-auto px-4 sm:px-6">
          <SectionHead eyebrow="How it works" title="From a one-line brief to a ready-to-send pipeline"
            text="No lists to buy, no spreadsheets to clean. Three steps, and most of the work is done for you." />
          <ol className="mt-14 grid gap-6 md:grid-cols-3">
            {STEPS.map(([title, text, Icon], i) => (
              <li key={title} className="relative rounded-2xl border border-border-subtle bg-background p-6 sm:p-7">
                <div className="flex items-center justify-between">
                  <span className="grid place-items-center size-11 rounded-xl bg-primary/10 text-primary"><Icon size={20} /></span>
                  <span className="text-4xl font-semibold text-foreground/10 tabular" aria-hidden="true">0{i + 1}</span>
                </div>
                <h3 className="mt-5 text-lg font-semibold text-foreground">{title}</h3>
                <p className="mt-2 text-[15px] text-muted-foreground leading-relaxed">{text}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      {/* Features */}
      <section id="features" className="scroll-mt-20 py-20 sm:py-28">
        <div className="max-w-6xl mx-auto px-4 sm:px-6">
          <SectionHead eyebrow="Features" title="Everything you need to win new clients"
            text="Discovery, research and outreach work together, so every lead arrives with context and a next step." />
          <div className="mt-14 grid gap-px rounded-2xl overflow-hidden border border-border-subtle bg-border-subtle sm:grid-cols-2 lg:grid-cols-3">
            {FEATURES.map(([title, text, Icon]) => (
              <div key={title} className="bg-background p-7">
                <Icon size={22} className="text-primary" />
                <h3 className="mt-4 text-base font-semibold text-foreground">{title}</h3>
                <p className="mt-2 text-[15px] text-muted-foreground leading-relaxed">{text}</p>
              </div>
            ))}
          </div>
          <div className="mt-10 rounded-2xl border border-border-subtle p-6 sm:p-7">
            <p className="text-sm font-semibold text-foreground flex items-center gap-2"><Sparkles size={16} className="text-primary" /> Also included</p>
            <ul className="mt-4 grid gap-x-6 gap-y-2.5 sm:grid-cols-2 lg:grid-cols-3 text-[15px] text-foreground/85">
              {ALSO.map((t) => <li key={t} className="flex items-start gap-2"><Check size={16} className="text-primary mt-0.5 shrink-0" />{t}</li>)}
            </ul>
          </div>
        </div>
      </section>

      {/* Benefits */}
      <section className="py-20 sm:py-24 bg-primary text-primary-foreground">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 grid gap-12 lg:grid-cols-[1fr_1.2fr] lg:items-center">
          <h2 className="text-3xl sm:text-4xl font-semibold tracking-tight text-balance">Spend your time on conversations, not spreadsheets.</h2>
          <ul className="grid gap-6 sm:grid-cols-2">
            {[
              ['Talk to the right person', 'Reach owners and managers by name instead of a generic inbox.'],
              ['Start every message with a reason', 'Drafts open with something specific about each business, not a sales pitch.'],
              ['Trust what you send', 'Every detail can be traced back to where it was found.'],
              ['Keep a steady flow', 'Daily searches keep your pipeline full without extra effort.'],
            ].map(([t, d]) => (
              <li key={t}>
                <p className="font-semibold flex items-center gap-2"><Check size={17} />{t}</p>
                <p className="mt-1.5 text-[15px] opacity-85 leading-relaxed">{d}</p>
              </li>
            ))}
          </ul>
        </div>
      </section>

      {/* Security */}
      <section id="security" className="scroll-mt-20 py-20 sm:py-28">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 grid gap-12 lg:grid-cols-2 lg:items-center">
          <SectionHead center={false} eyebrow="Privacy & control" title="Your workspace is yours alone"
            text="HOM is built so your data stays private and you stay in control of what goes out in your name." />
          <ul className="grid gap-4">
            {[
              [Lock, 'A private workspace for every account', 'Each account runs in its own workspace with its own database — nobody else can see your leads or messages.'],
              [KeyRound, 'Secure sign-in', 'Passwords are stored only as secure hashes, and new accounts confirm their email before signing in.'],
              [ShieldCheck, 'Nothing sent without you', 'Drafts wait for your approval, and anyone who asks not to be contacted is never contacted again.'],
            ].map(([Icon, t, d]) => (
              <li key={t} className="flex gap-4 rounded-2xl border border-border-subtle p-5">
                <span className="grid place-items-center size-10 shrink-0 rounded-xl bg-primary/10 text-primary"><Icon size={18} /></span>
                <div>
                  <p className="font-semibold text-foreground">{t}</p>
                  <p className="mt-1 text-[15px] text-muted-foreground leading-relaxed">{d}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </section>

      {/* Early access */}
      <section id="early-access" className="scroll-mt-20 py-20 sm:py-24 bg-surface/60 border-y border-border-subtle">
        <div className="max-w-3xl mx-auto px-4 sm:px-6 text-center">
          <SectionHead eyebrow="Early access" title="Start free today"
            text={`${name} is in early access. Create your account and start finding leads — no card needed. Plans will be announced before any pricing applies, and you’ll decide then.`} />
          <div className="mt-8 flex flex-col sm:flex-row justify-center gap-3">
            <Link to="/signup" className="btn-primary h-12 px-6 text-base">Create your account <ArrowRight size={16} /></Link>
            <Link to="/login" className="btn-secondary h-12 px-6 text-base">Log in</Link>
          </div>
        </div>
      </section>

      {/* FAQ */}
      <section id="faq" className="scroll-mt-20 py-20 sm:py-28">
        <div className="max-w-3xl mx-auto px-4 sm:px-6">
          <SectionHead eyebrow="FAQ" title="Questions, answered" />
          <div className="mt-12 divide-y divide-border-subtle border-y border-border-subtle">
            {FAQ.map(([q, a]) => (
              <details key={q} className="group py-5">
                <summary className="flex cursor-pointer list-none items-center justify-between gap-4 text-left text-[17px] font-medium text-foreground">
                  {q}
                  <ChevronDown size={18} className="shrink-0 text-muted-foreground transition-transform group-open:rotate-180" />
                </summary>
                <p className="mt-3 text-[15px] text-muted-foreground leading-relaxed pr-8">{a}</p>
              </details>
            ))}
          </div>
        </div>
      </section>

      {/* Final call to action */}
      <section className="pb-20 sm:pb-24">
        <div className="max-w-6xl mx-auto px-4 sm:px-6">
          <div className="rounded-3xl border border-border-subtle bg-surface px-6 py-12 sm:px-12 sm:py-16 text-center">
            <h2 className="text-3xl sm:text-4xl font-semibold tracking-tight text-foreground text-balance">Your next clients are out there. Let’s find them.</h2>
            <p className="mt-4 text-lg text-muted-foreground">Set up your account in a minute.</p>
            <Link to="/signup" className="btn-primary h-12 px-7 text-base mt-8 inline-flex">Get started free <ArrowRight size={16} /></Link>
          </div>
        </div>
      </section>
    </>
  )
}
