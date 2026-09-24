import { useEffect, useState } from 'react'
import { Eye, EyeOff, RefreshCw } from 'lucide-react'
import clsx from 'clsx'

// Shared pieces for sign-up, log-in and password reset.

export const PASSWORD_MIN = 10

// A simple, honest strength hint (the server enforces the real rules).
export function passwordStrength(pw) {
  if (!pw) return null
  if (pw.length < PASSWORD_MIN) return { label: `At least ${PASSWORD_MIN} characters`, level: 0 }
  let score = 0
  if (pw.length >= 14) score++
  if (/[a-z]/.test(pw) && /[A-Z]/.test(pw)) score++
  if (/\d/.test(pw)) score++
  if (/[^A-Za-z0-9]/.test(pw) || /\s/.test(pw)) score++
  if (new Set(pw.toLowerCase()).size < 5) score = 0
  return score >= 3 ? { label: 'Strong', level: 3 } : score >= 1 ? { label: 'Good', level: 2 } : { label: 'Okay — longer is better', level: 1 }
}

export function PasswordField({ id, label = 'Password', value, onChange, autoComplete = 'current-password', showStrength = false, autoFocus = false }) {
  const [show, setShow] = useState(false)
  const strength = showStrength ? passwordStrength(value) : null
  return (
    <div>
      <label htmlFor={id} className="label">{label}</label>
      <div className="relative">
        <input id={id} type={show ? 'text' : 'password'} className="input pr-11" autoComplete={autoComplete} required
          autoFocus={autoFocus} value={value} onChange={(e) => onChange(e.target.value)}
          minLength={showStrength ? PASSWORD_MIN : undefined} maxLength={72} />
        <button type="button" onClick={() => setShow((s) => !s)} aria-label={show ? 'Hide password' : 'Show password'}
          className="absolute inset-y-0 right-0 w-11 grid place-items-center text-muted-foreground hover:text-foreground">
          {show ? <EyeOff size={16} /> : <Eye size={16} />}
        </button>
      </div>
      {showStrength && (
        <div className="mt-2" aria-live="polite">
          <div className="grid grid-cols-3 gap-1" aria-hidden="true">
            {[1, 2, 3].map((i) => (
              <span key={i} className={clsx('h-1 rounded-full transition-colors',
                strength && strength.level >= i ? (strength.level === 1 ? 'bg-warning' : 'bg-success') : 'bg-secondary')} />
            ))}
          </div>
          <p className="text-meta mt-1.5">{strength ? strength.label : `Use ${PASSWORD_MIN}+ characters — a short phrase is easy to remember.`}</p>
        </div>
      )}
    </div>
  )
}

// 6-digit code entry with resend cooldown.
export function CodeForm({ email, submitLabel, busy, onSubmit, onResend, resending, onBack }) {
  const [code, setCode] = useState('')
  const [cooldown, setCooldown] = useState(60)
  useEffect(() => {
    if (cooldown <= 0) return undefined
    const t = setTimeout(() => setCooldown((c) => c - 1), 1000)
    return () => clearTimeout(t)
  }, [cooldown])
  return (
    <form className="space-y-4" onSubmit={(e) => { e.preventDefault(); onSubmit(code) }}>
      <p className="text-support">We sent a 6-digit code to <span className="text-foreground font-medium">{email}</span>. It works for 10 minutes.</p>
      <div>
        <label htmlFor="auth-code" className="label">Verification code</label>
        <input id="auth-code" inputMode="numeric" autoComplete="one-time-code" autoFocus maxLength={6}
          className="input h-12 text-center tracking-[0.6em] text-xl tabular" placeholder="••••••"
          value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))} />
      </div>
      <button type="submit" className="btn-primary w-full h-11" disabled={busy || code.length !== 6}>
        {busy && <RefreshCw size={14} className="animate-spin" />} {submitLabel}
      </button>
      <div className="flex items-center justify-between text-sm">
        <button type="button" className="text-muted-foreground hover:text-foreground" onClick={onBack}>Change email</button>
        <button type="button" className="font-semibold text-primary disabled:text-muted-foreground"
          disabled={cooldown > 0 || resending} onClick={() => { onResend(); setCooldown(60) }}>
          {cooldown > 0 ? `Resend in ${cooldown}s` : 'Resend code'}
        </button>
      </div>
      <p className="text-meta">Can't find it? Check your spam or promotions folder.</p>
    </form>
  )
}

export function FormError({ message }) {
  if (!message) return null
  return <p role="alert" className="rounded-xl bg-error/10 text-error text-sm px-3.5 py-2.5">{message}</p>
}

export function Notice({ children }) {
  return <p role="status" className="rounded-xl bg-warning/10 text-sm text-foreground px-3.5 py-2.5">{children}</p>
}
