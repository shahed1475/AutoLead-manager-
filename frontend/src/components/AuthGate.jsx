import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { LogoMark, BRAND } from './Logo'
import toast from 'react-hot-toast'
import { authApi, getSessionToken, setSessionToken } from '../api/client'
import { CLIENT_SIGN_IN, isClientEdition, setEdition } from '../lib/edition'

// Gates the whole app behind the optional local app password. If no
// password has ever been set (fresh install / dev workflow), the app stays
// open. Listens for the 'autolead:unauthorized' event the axios client
// dispatches on any 401, so an expired/invalid session re-locks the UI.
//
// In a client workspace (edition 'client') there is no password: the client
// signs in on the client link's sign-in page, which sends them here with a
// one-time link (?handoff=…) that this workspace exchanges for a session.

async function clientSignIn() {
  const params = new URLSearchParams(window.location.search)
  const handoff = params.get('handoff')
  if (handoff) {
    params.delete('handoff')
    const rest = params.toString()
    window.history.replaceState(null, '', window.location.pathname + (rest ? `?${rest}` : '') + window.location.hash)
    try {
      const { token } = await authApi.handoff(handoff)
      setSessionToken(token)
      return true
    } catch {
      setSessionToken('')
    }
  }
  if (getSessionToken()) return true
  window.location.replace(CLIENT_SIGN_IN)
  return false
}
export default function AuthGate({ children }) {
  const [status, setStatus] = useState('checking') // checking | locked | open
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function checkStatus() {
    try {
      const { password_set, edition } = await authApi.status()
      setEdition(edition)
      if (edition === 'client') {
        if (await clientSignIn()) setStatus('open')
        return
      }
      if (!password_set) {
        setStatus('open')
        return
      }
      setStatus(getSessionToken() ? 'open' : 'locked')
    } catch {
      setStatus('checking') // backend not reachable yet — keep retrying, don't lock the user out
    }
  }

  useEffect(() => {
    checkStatus()
    const onUnauthorized = () => {
      if (isClientEdition()) { window.location.replace(CLIENT_SIGN_IN); return }
      setStatus('locked')
    }
    window.addEventListener('autolead:unauthorized', onUnauthorized)
    return () => window.removeEventListener('autolead:unauthorized', onUnauthorized)
  }, [])

  useEffect(() => {
    if (status !== 'checking') return
    const t = setTimeout(checkStatus, 1500)
    return () => clearTimeout(t)
  }, [status])

  async function handleUnlock(e) {
    e.preventDefault()
    if (!password) return
    setSubmitting(true)
    try {
      const { token } = await authApi.unlock(password)
      setSessionToken(token)
      setPassword('')
      setStatus('open')
    } catch (err) {
      toast.error(err.message || 'Incorrect password')
    } finally {
      setSubmitting(false)
    }
  }

  if (status === 'open') return children

  if (status === 'checking') {
    return (
      <div className="h-screen flex items-center justify-center bg-background text-muted-foreground">
        <RefreshCw size={20} className="animate-spin" />
      </div>
    )
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <form onSubmit={handleUnlock} className="w-full max-w-[360px] animate-page-in">
        <div className="flex flex-col items-center text-center">
          <LogoMark size={52} />
          <h1 className="text-page mt-6">Welcome back</h1>
          <p className="text-support mt-1.5">Enter the password for {BRAND.name} to continue.</p>
        </div>

        <div className="surface-overlay mt-8 p-5 space-y-3">
          <label htmlFor="app-password" className="label">Password</label>
          <input
            id="app-password"
            type="password"
            autoFocus
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="input"
          />
          <button type="submit" disabled={submitting || !password} className="btn-primary w-full">
            {submitting && <RefreshCw size={14} className="animate-spin" />}
            Unlock
          </button>
        </div>
      </form>
    </div>
  )
}
