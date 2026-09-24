import { useEffect, useRef, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, Check, CornerDownLeft, Delete, Eye, EyeOff, Keyboard, RefreshCw, ShieldCheck, X } from 'lucide-react'
import toast from 'react-hot-toast'
import { socialApi } from '../../api/client'

// The live window of HOM's own browser for a LinkedIn / X account. You sign
// in yourself: click on the picture to click in the page, type in the box
// below. HOM never stores your password — only the site's sign-in cookies.

const W = 1280
const H = 800

export default function BrowserWindow({ account, onClose, onDone }) {
  const [src, setSrc] = useState(null)
  const [text, setText] = useState('')
  const [hide, setHide] = useState(true)
  const [error, setError] = useState(null)
  const [opening, setOpening] = useState(true)
  const img = useRef(null)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    let url = null
    let timer = null
    const load = async () => {
      try {
        const blob = await socialApi.browserScreen(account.id)
        if (!alive.current) return
        const next = URL.createObjectURL(blob)
        setSrc(next)
        if (url) URL.revokeObjectURL(url)
        url = next
        setError(null)
      } catch (e) {
        if (alive.current) setError(e.message)
      }
      if (alive.current) timer = setTimeout(load, 1200)
    }
    socialApi.browserOpen(account.id)
      .then(() => { setOpening(false); load() })
      .catch((e) => { setOpening(false); setError(e.message) })
    return () => { alive.current = false; clearTimeout(timer); if (url) URL.revokeObjectURL(url) }
  }, [account.id])

  const act = useMutation({ mutationFn: (a) => socialApi.browserAct(account.id, a), onError: (e) => toast.error(e.message) })
  const done = useMutation({
    mutationFn: () => socialApi.browserDone(account.id),
    onSuccess: (a) => {
      if (a.status === 'connected') { toast.success(`${a.name} is signed in`); onDone(); onClose() }
      else toast.error(a.last_error || 'Not signed in yet')
    },
    onError: (e) => toast.error(e.message),
  })

  const click = (e) => {
    const r = img.current.getBoundingClientRect()
    act.mutate({ type: 'click', x: Math.round(((e.clientX - r.left) / r.width) * W), y: Math.round(((e.clientY - r.top) / r.height) * H) })
  }
  const send = (e) => {
    e.preventDefault()
    if (!text) return
    act.mutate({ type: 'type', text })
    setText('')
  }
  const key = (k) => act.mutate({ type: 'key', key: k })

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center p-2 sm:p-6" role="dialog" aria-modal="true" aria-label={`Sign in to ${account.name}`}>
      <button className="absolute inset-0 bg-black/60" aria-label="Close" onClick={onClose} />
      <div className="relative w-full max-w-5xl max-h-full overflow-y-auto bg-surface-elevated border border-border rounded-2xl flex flex-col">
        <div className="flex items-center gap-3 px-5 h-14 border-b border-border-subtle">
          <ShieldCheck size={16} className="text-primary shrink-0" />
          <h2 className="text-sm font-semibold flex-1 truncate">Sign in to {account.platform === 'x' ? 'X' : 'LinkedIn'} — {account.name}</h2>
          <button className="btn-ghost h-8 w-8 px-0" aria-label="Close" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="p-4 space-y-3">
          <p className="text-meta">Click on the picture to click in the page, type in the box below (your password goes straight to the site — HOM doesn’t keep it). Finish any code or security check yourself, then press <b>I’m signed in</b>.</p>
          <div className="relative rounded-xl overflow-hidden border border-border-subtle bg-secondary" style={{ aspectRatio: `${W} / ${H}` }}>
            {src && <img ref={img} src={src} alt="The browser window" className="w-full h-full cursor-crosshair select-none" draggable={false} onClick={click} />}
            {(opening || (!src && !error)) && <div className="absolute inset-0 flex items-center justify-center text-support gap-2"><RefreshCw size={16} className="animate-spin" /> Opening the browser…</div>}
            {error && !src && <div className="absolute inset-0 flex items-center justify-center text-error text-sm p-6 text-center">{error}</div>}
          </div>
          <form className="flex flex-wrap gap-2" onSubmit={send} autoComplete="off">
            <div className="relative flex-1 min-w-[12rem]">
              <Keyboard size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
              <input className="input h-10 pl-9 pr-10" placeholder="Click a field in the page, then type here and press Type" value={text}
                onChange={(e) => setText(e.target.value)} aria-label="Text to type" type={hide ? 'password' : 'text'} spellCheck={false} autoComplete="off" />
              <button type="button" className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground p-1" aria-label={hide ? 'Show text' : 'Hide text'} onClick={() => setHide(!hide)}>
                {hide ? <Eye size={14} /> : <EyeOff size={14} />}
              </button>
            </div>
            <button className="btn-secondary h-10" disabled={!text || act.isPending}>Type</button>
            <button type="button" className="btn-ghost h-10" onClick={() => key('Enter')} title="Enter"><CornerDownLeft size={14} /> Enter</button>
            <button type="button" className="btn-ghost h-10" onClick={() => key('Tab')}>Tab</button>
            <button type="button" className="btn-ghost h-10 px-2" onClick={() => key('Backspace')} aria-label="Backspace"><Delete size={14} /></button>
            <button type="button" className="btn-ghost h-10 px-2" onClick={() => act.mutate({ type: 'scroll', dy: -500 })} aria-label="Scroll up"><ArrowUp size={14} /></button>
            <button type="button" className="btn-ghost h-10 px-2" onClick={() => act.mutate({ type: 'scroll', dy: 500 })} aria-label="Scroll down"><ArrowDown size={14} /></button>
          </form>
          <div className="flex flex-wrap items-center justify-end gap-2 border-t border-border-subtle pt-3">
            <span className="text-meta flex-1">HOM stops and asks you here whenever the site shows a security check — it never tries to get around one.</span>
            <button className="btn-primary h-10" disabled={done.isPending} onClick={() => done.mutate()}>
              {done.isPending ? <RefreshCw size={14} className="animate-spin" /> : <Check size={14} />} I’m signed in
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
