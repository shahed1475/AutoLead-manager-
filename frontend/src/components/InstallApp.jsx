import { useEffect, useState } from 'react'
import { Download, Share, SquarePlus, X } from 'lucide-react'
import toast from 'react-hot-toast'
import { useInstall } from '../lib/install'
import { LogoMark, BRAND } from './Logo'

// iPhone / iPad: Safari has no install prompt, so explain the two taps.
function IosInstallSheet({ onClose }) {
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  const steps = [
    [Share, <>Tap <b>Share</b> in Safari's toolbar.</>],
    [SquarePlus, <>Choose <b>Add to Home Screen</b>.</>],
    [Download, <>Tap <b>Add</b>. {BRAND.name} now opens from your home screen like an app.</>],
  ]
  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/40 p-0 sm:p-4" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label={`Install ${BRAND.name}`}
        className="surface-overlay w-full sm:max-w-sm rounded-b-none sm:rounded-3xl p-5 pb-safe animate-page-in"
        onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <LogoMark size={40} />
            <div>
              <p className="text-section">Install {BRAND.name}</p>
              <p className="text-meta">Use it like an app on your iPhone or iPad</p>
            </div>
          </div>
          <button onClick={onClose} className="btn-ghost h-8 w-8 px-0" aria-label="Close"><X size={16} /></button>
        </div>
        <ol className="mt-5 space-y-3">
          {steps.map(([Icon, text], i) => (
            <li key={i} className="flex items-center gap-3 text-sm">
              <span className="grid place-items-center w-8 h-8 rounded-full bg-secondary text-primary shrink-0"><Icon size={16} /></span>
              <span>{text}</span>
            </li>
          ))}
        </ol>
        <p className="text-meta mt-4">This works in Safari. In other iPhone browsers, open this page in Safari first.</p>
        <button onClick={onClose} className="btn-primary w-full mt-5 mb-2">Got it</button>
      </div>
    </div>
  )
}

// "Install app" entry — renders nothing when installing isn't possible here
// or the app is already installed.
export default function InstallApp({ className = '' }) {
  const { state, install } = useInstall()
  const [iosOpen, setIosOpen] = useState(false)
  if (state === 'installed' || state === 'unavailable') return null

  async function onClick() {
    if (state === 'ios') { setIosOpen(true); return }
    const accepted = await install()
    if (accepted) toast.success(`${BRAND.name} installed`)
  }

  return (
    <>
      <button type="button" onClick={onClick}
        className={`group flex items-center gap-2.5 h-8 px-2.5 w-full rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-secondary/70 transition-colors ${className}`}>
        <Download size={16} strokeWidth={1.75} className="shrink-0" />
        <span className="flex-1 text-left">Install app</span>
      </button>
      {iosOpen && <IosInstallSheet onClose={() => setIosOpen(false)} />}
    </>
  )
}
