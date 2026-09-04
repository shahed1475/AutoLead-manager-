import { useRef, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { Upload, FileSpreadsheet, X, AlertTriangle, Check } from 'lucide-react'
import toast from 'react-hot-toast'
import { automationApi } from '../../api/client'

export default function AutomationUpload({ onImported, compact = false }) {
  const fileRef = useRef(null)
  const [preview, setPreview] = useState(null)

  const previewMut = useMutation({
    mutationFn: (files) => {
      const fd = new FormData()
      fd.append('file', files[0])
      if (files[1]) fd.append('file2', files[1])
      return automationApi.previewImport(fd)
    },
    onSuccess: (p) => setPreview({ ...p, filename: 'upload' }),
    onError: (err) => toast.error(err?.response?.data?.detail || 'Could not read that file'),
  })

  const confirmMut = useMutation({
    mutationFn: () => automationApi.confirmImport({
      locations: preview.locations, niches: preview.niches,
      filename: preview.filename || 'upload', layout: preview.layout,
    }),
    onSuccess: (status) => { setPreview(null); toast.success('Search queue created'); onImported?.(status) },
    onError: (err) => toast.error(err?.response?.data?.detail || 'Import failed'),
  })

  function onPick(e) {
    const files = [...e.target.files]
    if (files.length) previewMut.mutate(files)
    e.target.value = ''
  }

  return (
    <>
      {compact ? (
        <button onClick={() => fileRef.current?.click()} disabled={previewMut.isPending}
          className="btn-secondary text-xs inline-flex items-center gap-1.5">
          <Upload size={13} /> {previewMut.isPending ? 'Reading…' : 'Import another file'}
        </button>
      ) : (
        <div className="rounded-xl border border-dashed border-slate-700 bg-slate-900/30 p-8 text-center">
          <FileSpreadsheet size={28} className="mx-auto text-brand-400" />
          <p className="mt-2 text-sm font-medium text-slate-200">Upload a locations &amp; niches file</p>
          <p className="mt-1 text-xs text-slate-500">
            CSV or XLSX. Columns: <code>City/Town</code>, <code>State</code>, <code>Niche</code> — or
            separate location and niche sheets. (.xls is not supported.)
          </p>
          <button onClick={() => fileRef.current?.click()} disabled={previewMut.isPending}
            className="btn-primary mt-4 inline-flex items-center gap-2 text-sm">
            <Upload size={14} /> {previewMut.isPending ? 'Reading…' : 'Choose file(s)'}
          </button>
        </div>
      )}
      <input ref={fileRef} type="file" accept=".csv,.xlsx" multiple hidden onChange={onPick} />

      {preview && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={() => setPreview(null)}>
          <div className="card w-full max-w-lg p-5 space-y-4 text-left" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-bold text-slate-100">Import preview</h3>
              <button onClick={() => setPreview(null)} className="p-1 text-slate-400 hover:bg-slate-800 rounded"><X size={15} /></button>
            </div>
            <ul className="text-xs text-slate-300 space-y-1">
              <li><Check size={11} className="inline text-emerald-400" /> {preview.n_locations} locations</li>
              <li><Check size={11} className="inline text-emerald-400" /> {preview.n_niches} niches</li>
              <li><Check size={11} className="inline text-emerald-400" /> {preview.combinations.toLocaleString()} search combinations</li>
              <li className="text-slate-500">Estimated ≥ {preview.estimated_days} day(s) — {preview.estimate_note}</li>
            </ul>
            {preview.warnings?.length > 0 && (
              <div className="text-xs text-amber-400 flex items-start gap-1.5">
                <AlertTriangle size={12} className="mt-0.5 shrink-0" />
                <span>{preview.warnings.join(' ')}</span>
              </div>
            )}
            <div className="grid grid-cols-2 gap-3 text-xs">
              <div>
                <p className="font-semibold text-slate-400 mb-1">Locations</p>
                <div className="max-h-40 overflow-y-auto text-slate-400 space-y-0.5">
                  {preview.locations.slice(0, 200).map((l, i) => <div key={i}>{[l.city, l.state].filter(Boolean).join(', ')}</div>)}
                </div>
              </div>
              <div>
                <p className="font-semibold text-slate-400 mb-1">Niches</p>
                <div className="max-h-40 overflow-y-auto text-slate-400 space-y-0.5">
                  {preview.niches.map((n, i) => <div key={i}>{n}</div>)}
                </div>
              </div>
            </div>
            <div className="flex justify-end gap-2">
              <button onClick={() => setPreview(null)} className="btn-secondary text-xs">Cancel</button>
              <button onClick={() => confirmMut.mutate()} disabled={confirmMut.isPending} className="btn-primary text-xs">
                {confirmMut.isPending ? 'Importing…' : 'Confirm Import'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
