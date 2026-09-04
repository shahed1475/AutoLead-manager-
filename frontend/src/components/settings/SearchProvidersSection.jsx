import { useQuery } from '@tanstack/react-query'
import { Search } from 'lucide-react'
import { leadSearchApi } from '../../api/client'

const STATUS_CLS = {
  available:               'bg-emerald-500/15 text-emerald-400 border-emerald-500/30',
  configured:              'bg-sky-500/15 text-sky-400 border-sky-500/30',
  not_configured:          'bg-amber-500/15 text-amber-400 border-amber-500/30',
  api_required:            'bg-slate-500/15 text-slate-400 border-slate-500/30',
  unsupported:             'bg-slate-700/40 text-slate-500 border-slate-700',
  temporarily_unavailable: 'bg-rose-500/15 text-rose-400 border-rose-500/30',
}

const STATUS_LABEL = {
  available: 'Available', configured: 'Configured', not_configured: 'Needs setup',
  api_required: 'API key required', unsupported: 'Unsupported',
  temporarily_unavailable: 'Blocked',
}

const KIND_LABEL = {
  directory: 'Business directories', web_search: 'Search engines',
  api: 'API / self-hosted', meta: 'Meta / legacy', ai: 'AI search',
}

export default function SearchProvidersSection() {
  const { data } = useQuery({ queryKey: ['lead-search-providers'], queryFn: leadSearchApi.providers })
  const providers = data?.providers ?? []
  const groups = providers.reduce((acc, p) => {
    (acc[p.kind] ||= []).push(p)
    return acc
  }, {})

  return (
    <div className="card p-5 space-y-4">
      <div className="flex items-center gap-2.5">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-600/15 border border-brand-600/25">
          <Search size={15} className="text-brand-400" />
        </div>
        <div>
          <h3 className="text-sm font-semibold text-slate-100">Search Providers</h3>
          <p className="text-[11px] text-slate-500">
            Which engines Lead Search can use. Providers behind a paid API or hard anti-bot protection are
            listed but not used.
          </p>
        </div>
      </div>

      {Object.entries(groups).map(([kind, list]) => (
        <div key={kind}>
          <p className="mb-1.5 text-[10px] font-bold uppercase tracking-widest text-slate-600">
            {KIND_LABEL[kind] || kind}
          </p>
          <div className="flex flex-wrap gap-1.5">
            {list.map((p) => (
              <span
                key={p.id}
                title={p.requires_key ? `Set ${p.key_setting} to enable` : STATUS_LABEL[p.status]}
                className={`inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px] ${STATUS_CLS[p.status] || STATUS_CLS.unsupported}`}
              >
                {p.name}
                <span className="opacity-70">· {STATUS_LABEL[p.status] || p.status}</span>
              </span>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
