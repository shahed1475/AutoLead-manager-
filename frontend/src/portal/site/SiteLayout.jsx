import { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { Menu, Moon, Sun, X } from 'lucide-react'
import clsx from 'clsx'
import { LogoMark } from '../../components/Logo'
import { useTheme } from '../../lib/theme'
import { getToken } from '../api'
import { usePortalInfo } from '../hooks'

const NAV = [['Features', '/#features'], ['How it works', '/#how-it-works'], ['Security', '/#security'], ['Early access', '/#early-access'], ['FAQ', '/#faq']]

export function ThemeSwitch() {
  const { theme, toggle } = useTheme()
  return (
    <button type="button" onClick={toggle} className="btn-ghost h-9 w-9 px-0" aria-label={theme === 'light' ? 'Switch to night mode' : 'Switch to day mode'}>
      {theme === 'light' ? <Moon size={16} /> : <Sun size={16} />}
    </button>
  )
}

export function Brand({ className = '' }) {
  const { name } = usePortalInfo()
  return (
    <Link to="/" className={clsx('flex items-center gap-2.5 shrink-0', className)} aria-label={`${name} home`}>
      <LogoMark size={28} />
      <span className="text-[17px] font-bold tracking-tight text-foreground">{name}</span>
    </Link>
  )
}

function SiteHeader() {
  const [open, setOpen] = useState(false)
  const [scrolled, setScrolled] = useState(false)
  const { pathname, hash } = useLocation()
  const signedIn = !!getToken()
  useEffect(() => setOpen(false), [pathname, hash])
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8)
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => window.removeEventListener('scroll', onScroll)
  }, [])
  return (
    <header className={clsx('sticky top-0 z-30 pt-safe transition-colors',
      scrolled || open ? 'bg-background/90 backdrop-blur-md border-b border-border-subtle' : 'bg-transparent border-b border-transparent')}>
      <div className="max-w-6xl mx-auto h-16 px-4 sm:px-6 flex items-center gap-6">
        <Brand />
        <nav aria-label="Main" className="hidden lg:flex items-center gap-1 text-sm">
          {NAV.map(([label, to]) => (
            <a key={to} href={to} className="px-3 py-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-secondary/70 transition-colors">{label}</a>
          ))}
        </nav>
        <div className="flex-1" />
        <ThemeSwitch />
        {signedIn ? (
          <Link to="/account" className="btn-primary h-9 px-4 text-sm hidden sm:inline-flex">Open dashboard</Link>
        ) : (
          <div className="hidden sm:flex items-center gap-2">
            <Link to="/login" className="btn-ghost h-9 px-3.5 text-sm">Log in</Link>
            <Link to="/signup" className="btn-primary h-9 px-4 text-sm">Get started</Link>
          </div>
        )}
        <button type="button" className="btn-ghost h-9 w-9 px-0 lg:hidden" aria-expanded={open} aria-controls="mobile-nav"
          aria-label={open ? 'Close menu' : 'Open menu'} onClick={() => setOpen((o) => !o)}>
          {open ? <X size={18} /> : <Menu size={18} />}
        </button>
      </div>
      {open && (
        <nav id="mobile-nav" aria-label="Mobile" className="lg:hidden border-t border-border-subtle bg-background px-4 pb-4 pt-2">
          <div className="max-w-6xl mx-auto flex flex-col">
            {NAV.map(([label, to]) => (
              <a key={to} href={to} onClick={() => setOpen(false)} className="py-3 text-[15px] text-foreground border-b border-border-subtle">{label}</a>
            ))}
            <div className="grid grid-cols-2 gap-2 pt-4">
              {signedIn ? (
                <Link to="/account" className="btn-primary h-11 col-span-2">Open dashboard</Link>
              ) : (
                <>
                  <Link to="/login" className="btn-secondary h-11">Log in</Link>
                  <Link to="/signup" className="btn-primary h-11">Get started</Link>
                </>
              )}
            </div>
          </div>
        </nav>
      )}
    </header>
  )
}

function SiteFooter() {
  const { name, contact_email: contact } = usePortalInfo()
  return (
    <footer className="border-t border-border-subtle mt-8">
      <div className="max-w-6xl mx-auto px-4 sm:px-6 py-12 grid gap-10 sm:grid-cols-[1.4fr_1fr_1fr]">
        <div className="space-y-3 max-w-xs">
          <Brand />
          <p className="text-sm text-muted-foreground leading-relaxed">The sales growth engine: find the right businesses, reach the people who decide, and follow up — from one private workspace.</p>
        </div>
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Product</p>
          <ul className="mt-3 space-y-2 text-sm">
            <li><a href="/#features" className="text-foreground/80 hover:text-foreground">Features</a></li>
            <li><a href="/#how-it-works" className="text-foreground/80 hover:text-foreground">How it works</a></li>
            <li><a href="/#early-access" className="text-foreground/80 hover:text-foreground">Early access</a></li>
          </ul>
        </div>
        <div>
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Account</p>
          <ul className="mt-3 space-y-2 text-sm">
            <li><Link to="/login" className="text-foreground/80 hover:text-foreground">Log in</Link></li>
            <li><Link to="/signup" className="text-foreground/80 hover:text-foreground">Create an account</Link></li>
            <li><Link to="/privacy" className="text-foreground/80 hover:text-foreground">Privacy</Link></li>
            {contact && <li className="text-muted-foreground">Contact: <span className="text-foreground/80 select-all">{contact}</span></li>}
          </ul>
        </div>
      </div>
      <div className="border-t border-border-subtle">
        <p className="max-w-6xl mx-auto px-4 sm:px-6 py-5 text-meta">© {new Date().getFullYear()} {name}. All rights reserved.</p>
      </div>
    </footer>
  )
}

export default function SiteLayout({ children }) {
  return (
    <div className="min-h-[100dvh] bg-background flex flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:top-3 focus:left-3 focus:z-50 btn-primary h-9">Skip to content</a>
      <SiteHeader />
      <main id="main" className="flex-1">{children}</main>
      <SiteFooter />
    </div>
  )
}
