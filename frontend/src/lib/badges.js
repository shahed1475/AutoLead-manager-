// Shared badge class maps — previously duplicated across LeadTable.jsx and
// Campaign.jsx independently. Single source of truth for status/channel/
// campaign-run-status colors so they can't drift out of sync.

export const CHANNEL_BADGE = {
  EMAIL:    'badge-email',
  WHATSAPP: 'badge-whatsapp',
  BOTH:     'badge bg-teal-500/20 text-teal-400 border border-teal-500/30',
}

export const STATUS_BADGE = {
  PENDING:        'badge-pending',
  ENRICHED:       'badge-enriched',
  SCORED:         'badge-scored',
  MESSAGES_READY: 'badge-messages-ready',
  SENT:           'badge-sent',
  REPLIED:        'badge-replied',
  SKIPPED:        'badge-skipped',
  DO_NOT_CONTACT: 'badge bg-rose-600/20 text-rose-400 border border-rose-600/30',
  FAILED:         'badge-failed',
}

export const SOURCE_BADGE = {
  GOOGLE_MAPS:   'badge bg-blue-500/20 text-blue-400 border border-blue-500/30',
  YELP:          'badge bg-red-500/20 text-red-400 border border-red-500/30',
  YELLOW_PAGES:  'badge bg-yellow-500/20 text-yellow-400 border border-yellow-500/30',
  GOOGLE_SEARCH: 'badge bg-purple-500/20 text-purple-400 border border-purple-500/30',
  BING_SEARCH:   'badge bg-sky-500/20 text-sky-400 border border-sky-500/30',
  HOTFROG:       'badge bg-orange-500/20 text-orange-400 border border-orange-500/30',
  FOURSQUARE:    'badge bg-pink-500/20 text-pink-400 border border-pink-500/30',
  TOP_LIST:      'badge bg-teal-500/20 text-teal-400 border border-teal-500/30',
  GENERIC_DIR:   'badge bg-slate-500/20 text-slate-400 border border-slate-500/30',
}

export const SOURCE_LABEL = {
  GOOGLE_MAPS:   '🗺 Maps',
  YELP:          '⭐ Yelp',
  YELLOW_PAGES:  '📒 YP',
  GOOGLE_SEARCH: '🔍 Search',
  BING_SEARCH:   '🔎 Bing',
  HOTFROG:       '🔥 Hotfrog',
  FOURSQUARE:    '📍 4sq',
  TOP_LIST:      '📰 List',
  GENERIC_DIR:   '📂 Dir',
}

// campaign_runs.status colors
export const RUN_STATUS_CLS = {
  RUNNING:   'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30',
  COMPLETED: 'bg-blue-500/20 text-blue-400 border border-blue-500/30',
  STOPPED:   'bg-amber-500/20 text-amber-400 border border-amber-500/30',
  FAILED:    'bg-red-500/20 text-red-400 border border-red-500/30',
}
