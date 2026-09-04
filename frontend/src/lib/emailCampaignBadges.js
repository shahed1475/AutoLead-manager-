// Badge class maps for the Email Campaigns module (kept out of the shared
// badges.js so it stays a self-contained preview feature).

export const CAMPAIGN_STATUS_BADGE = {
  DRAFT:     'badge bg-slate-500/20 text-slate-400 border border-slate-500/30',
  READY:     'badge bg-amber-500/20 text-amber-400 border border-amber-500/30',
  RUNNING:   'badge bg-emerald-500/20 text-emerald-400 border border-emerald-500/30',
  PAUSED:    'badge bg-sky-500/20 text-sky-400 border border-sky-500/30',
  COMPLETED: 'badge bg-blue-500/20 text-blue-400 border border-blue-500/30',
  FAILED:    'badge bg-red-500/20 text-red-400 border border-red-500/30',
}

export const LEAD_STATUS_BADGE = {
  VALIDATED:            'badge bg-slate-500/20 text-slate-400 border border-slate-500/30',
  GENERATED:            'badge bg-violet-500/20 text-violet-400 border border-violet-500/30',
  SENT:                 'badge bg-teal-500/20 text-teal-400 border border-teal-500/30',
  SEND_FAILED:          'badge bg-red-900/30 text-red-500 border border-red-800/40',
  SEND_BLOCKED:         'badge bg-red-500/20 text-red-400 border border-red-500/30',
  AI_GENERATION_FAILED: 'badge bg-amber-500/20 text-amber-400 border border-amber-500/30',
  MISSING_EMAIL:        'badge bg-slate-600/20 text-slate-400 border border-slate-600/30',
  INVALID_EMAIL:        'badge bg-slate-600/20 text-slate-400 border border-slate-600/30',
  DUPLICATE:            'badge bg-slate-600/20 text-slate-400 border border-slate-600/30',
  DO_NOT_CONTACT:       'badge bg-rose-600/20 text-rose-400 border border-rose-600/30',
}
