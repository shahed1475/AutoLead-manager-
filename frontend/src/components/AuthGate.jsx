import { useEffect, useState } from 'react'
import { Lock, RefreshCw, ShieldCheck } from 'lucide-react'
import toast from 'react-hot-toast'
import { authApi, getSessionToken, setSessionToken } from '../api/client'

// Gates the whole app behind the optional local app password. If no
// password has ever been set (fresh install / dev workflow), the app stays
// open. Listens for the 'autolead:unauthorized' event the axios client
// dispatches on any 401, so an expired/invalid session re-locks the UI.
export default function AuthGate({ children }) {
  const [status, setStatus] = useState('checking') // checking | locked | open
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function checkStatus() {
    try {
      const { password_set } = await authApi.status()
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
    const onUnauthorized = () => setStatus('locked')
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
      <div className="h-screen flex items-center justify-center bg-slate-950 text-slate-500">
        <RefreshCw size={20} className="animate-spin" />
      </div>
    )
  }

  return (
    <div className="h-screen flex items-center justify-center bg-slate-950 px-4">
      <form
        onSubmit={handleUnlock}
        className="w-full max-w-sm card p-6 space-y-4 border border-slate-700/50"
      >
        <div className="flex flex-col items-center gap-2 text-center">
          <div className="w-11 h-11 rounded-xl bg-brand-500/15 ring-1 ring-brand-500/40 flex items-center justify-center">
            <Lock size={18} className="text-brand-400" />
          </div>
          <h1 className="text-base font-bold text-slate-100">AutoLead is locked</h1>
          <p className="text-xs text-slate-500">Enter the app password to continue.</p>
        </div>

        <input
          type="password"
          autoFocus
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder="Password"
          className="w-full px-3 py-2.5 rounded-lg bg-slate-900 border border-slate-700 text-sm text-slate-200
                     focus:outline-none focus:ring-2 focus:ring-brand-500/50"
        />

        <button
          type="submit"
          disabled={submitting || !password}
          className="w-full py-2.5 rounded-lg font-bold text-sm uppercase tracking-widest
                     bg-brand-600 hover:bg-brand-500 text-white disabled:opacity-40 disabled:cursor-not-allowed
                     flex items-center justify-center gap-2 transition-colors"
        >
          {submitting ? <RefreshCw size={14} className="animate-spin" /> : <ShieldCheck size={14} />}
          Unlock
        </button>
      </form>
    </div>
  )
}
