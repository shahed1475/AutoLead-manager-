import axios from 'axios'

const TOKEN_KEY = 'autolead_session_token'

export function getSessionToken() {
  return localStorage.getItem(TOKEN_KEY) || ''
}

export function setSessionToken(token) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

// Appends the session token as a query param — used for the SSE log stream,
// since the native EventSource API can't set an Authorization header.
export function withSessionToken(url) {
  const token = getSessionToken()
  if (!token) return url
  const sep = url.includes('?') ? '&' : '?'
  return `${url}${sep}token=${encodeURIComponent(token)}`
}

const api = axios.create({
  baseURL: '/api',
  timeout: 300000,  // 5 min — Ollama generation can take up to 300 s
})

api.interceptors.request.use((config) => {
  const token = getSessionToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401) {
      setSessionToken('')
      window.dispatchEvent(new CustomEvent('autolead:unauthorized'))
    }
    const msg = err.response?.data?.detail || err.response?.data?.message || err.message
    const e = new Error(msg)
    e.status = err.response?.status   // lets callers treat e.g. 404 as "nothing yet"
    return Promise.reject(e)
  }
)

export const leadsApi = {
  list: (params) => api.get('/leads', { params }).then((r) => r.data),
  get: (id) => api.get(`/leads/${id}`).then((r) => r.data),
  create: (data) => api.post('/leads', data).then((r) => r.data),
  update: (id, data) => api.put(`/leads/${id}`, data).then((r) => r.data),
  delete: (id) => api.delete(`/leads/${id}`),
  skip: (id) => api.post(`/leads/${id}/skip`).then((r) => r.data),
  importCsv: (file) => {
    const form = new FormData()
    form.append('file', file)
    return api.post('/leads/import/csv', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }).then((r) => r.data)
  },
  exportCsv: (params = {}) =>
    api.get('/leads/export/csv', { params, responseType: 'blob' }).then((r) => r.data),
  resend: (id, channel = 'EMAIL') =>
    api.post(`/leads/${id}/resend`, null, { params: { channel } }).then((r) => r.data),
  updateStatus: (id, status) =>
    api.patch(`/leads/${id}/status`, { status }).then((r) => r.data),
  deleteAll: (status) =>
    api.delete('/leads', { params: status ? { status } : {} }).then((r) => r.data),
  research: (leadIds, submissionSource = 'manual', targetTitles = null) =>
    api.post('/leads/research', {
      lead_ids: leadIds, submission_source: submissionSource,
      ...(targetTitles?.length ? { target_titles: targetTitles } : {}),
    }).then((r) => r.data),
  researchOne: (id, targetTitles = null) =>
    api.post(`/leads/${id}/research`, targetTitles?.length ? { target_titles: targetTitles } : undefined).then((r) => r.data),
  researchExclude: (id, excluded) =>
    api.post(`/leads/${id}/research-exclude`, { excluded }).then((r) => r.data),
}

export const leadSearchApi = {
  providers: () => api.get('/lead-search/providers').then((r) => r.data),
}

export const aiApi = {
  status: () => api.get('/ai/status').then((r) => r.data),
  generate: (leadId, messageType = 'all') =>
    api.post('/ai/generate', { lead_id: leadId, message_type: messageType }).then((r) => r.data),
  generateBulk: (leadIds) =>
    api.post('/ai/generate-bulk', { lead_ids: leadIds }).then((r) => r.data),
  testPrompt: (prompt) =>
    api.post('/ai/test-prompt', { prompt }).then((r) => r.data),
  testBusiness: (businessText) =>
    api.post('/ai/test', { business_text: businessText }).then((r) => r.data),
}

export const campaignApi = {
  send: (leadId, channel) =>
    api.post(`/campaign/send/${leadId}`, null, { params: { channel } }).then((r) => r.data),
  sendFollowup: (leadId, channel) =>
    api.post(`/campaign/send-followup/${leadId}`, null, { params: { channel } }).then((r) => r.data),
  markReplied: (leadId) =>
    api.post(`/campaign/mark-replied/${leadId}`).then((r) => r.data),
  bulkSend: (leadIds, channel) =>
    api.post('/campaign/bulk-send', { lead_ids: leadIds, channel }).then((r) => r.data),
  stats: () => api.get('/campaign/stats').then((r) => r.data),
  start: (payload) => api.post('/campaign/start', payload).then((r) => r.data),
  stop: () => api.post('/campaign/stop').then((r) => r.data),
  pause: () => api.post('/campaign/pause').then((r) => r.data),
  resume: () => api.post('/campaign/resume').then((r) => r.data),
  history: (limit = 50) => api.get('/campaign/history', { params: { limit } }).then((r) => r.data),
  historyDetail: (runId) => api.get(`/campaign/history/${runId}`).then((r) => r.data),
  deleteHistory: (runId) => api.delete(`/campaign/history/${runId}`).then((r) => r.data),
  testPipeline: () => api.post('/campaign/test-pipeline').then((r) => r.data),
  dbHealth: () => api.get('/campaign/db-health').then((r) => r.data),
}

export const scraperApi = {
  search: (payload) => api.post('/scraper/search', payload).then((r) => r.data),
  status: () => api.get('/scraper/status').then((r) => r.data),
}

export const settingsApi = {
  getAll: () => api.get('/settings').then((r) => r.data),
  update: (key, value) => api.put('/settings', { key, value }).then((r) => r.data),
  bulkUpdate: (data) => api.put('/settings/bulk', data).then((r) => r.data),
  testSmtp: () => api.post('/settings/test-smtp').then((r) => r.data),
  getDna: () => api.get('/settings/dna').then((r) => r.data),
  saveDna: (content) => api.put('/settings/dna', { content }).then((r) => r.data),
  resetStats: () => api.post('/settings/reset-stats').then((r) => r.data),
}

export const statsApi = {
  dashboard: () => api.get('/stats').then((r) => r.data),
  weekly: () => api.get('/stats/weekly').then((r) => r.data),
}

export const engineApi = {
  status: () => api.get('/engine/status').then((r) => r.data),
  runNow: () => api.post('/engine/run-now').then((r) => r.data),
}

export const logsApi = {
  recent: (limit = 20) => api.get('/logs', { params: { limit } }).then((r) => r.data),
}

export const inboxApi = {
  list:    (params = {})          => api.get('/inbox', { params }).then((r) => r.data),
  stats:   ()                     => api.get('/inbox/stats').then((r) => r.data),
  summary: ()                     => api.get('/inbox/summary').then((r) => r.data),
  process: (id, intent)           => api.post(`/inbox/${id}/process`, null, { params: { intent } }).then((r) => r.data),
  check:   ()                     => api.post('/inbox/check').then((r) => r.data),
}

export const repliesApi = {
  drafts:  ()                     => api.get('/replies/drafts').then((r) => r.data),
  edit:    (id, fields)           => api.put(`/replies/${id}/draft`, fields).then((r) => r.data),
  approve: (id)                   => api.post(`/replies/${id}/approve`).then((r) => r.data),
  discard: (id)                   => api.post(`/replies/${id}/discard`).then((r) => r.data),
}

export const enrichApi = {
  enrichLead:    (leadId)         => api.post(`/leads/${leadId}/enrich-sync`).then((r) => r.data),
  enrichQueue:   (leadId)         => api.post(`/leads/${leadId}/enrich`).then((r) => r.data),
  getEnrichment: (leadId)         => api.get(`/leads/${leadId}/enrichment`).then((r) => r.data),
  scoreAll:      (leadIds = null) => api.post('/leads/score-all', { lead_ids: leadIds }).then((r) => r.data),
  scoreDist:     ()               => api.get('/leads/score-dist').then((r) => r.data),
}

// Business Intelligence — pain points & business-ease opportunities, built on
// top of the existing research/company_profile pipeline. 404s are expected
// (and handled by the caller) whenever research hasn't completed for a lead yet.
export const intelligenceApi = {
  // 404 = lead not researched yet — an empty state, not a failure
  getIntelligence:  (leadId) => api.get(`/leads/${leadId}/intelligence`).then((r) => r.data)
    .catch((e) => { if (e.status === 404) return null; throw e }),
  getPainPoints:    (leadId) => api.get(`/leads/${leadId}/pain-points`).then((r) => r.data),
  getOpportunities: (leadId) => api.get(`/leads/${leadId}/opportunities`).then((r) => r.data),
  getSolutions:     (leadId) => api.get(`/leads/${leadId}/solutions`).then((r) => r.data),
  getEvidence:      (leadId) => api.get(`/leads/${leadId}/evidence`).then((r) => r.data),
  analyzePainPoints:   (leadId) => api.post(`/leads/${leadId}/pain-points/analyze`).then((r) => r.data),
  analyzeOpportunities: (leadId) => api.post(`/leads/${leadId}/opportunities/analyze`).then((r) => r.data),
}

// Marketing Agent — draft outreach generated from the real evidence chain
// above, gated behind human approval. Approving only stages content into the
// existing leads.ai_email_*/ai_whatsapp_msg fields; the existing Send button
// (LeadTable/campaigns) is still what actually delivers anything.
export const marketingApi = {
  generate: (leadId)              => api.post(`/leads/${leadId}/messages/generate`).then((r) => r.data),
  list:     (leadId)              => api.get(`/leads/${leadId}/messages`).then((r) => r.data),
  edit:     (leadId, msgId, fields) => api.put(`/leads/${leadId}/messages/${msgId}`, fields).then((r) => r.data),
  approve:  (leadId, msgId)       => api.post(`/leads/${leadId}/messages/${msgId}/approve`).then((r) => r.data),
  reject:   (leadId, msgId, reason) => api.post(`/leads/${leadId}/messages/${msgId}/reject`, { reason }).then((r) => r.data),
}

export const pipelineApi = {
  board: () => api.get('/pipeline/board').then((r) => r.data),
  moveStage: (leadId, toStatus, reason) =>
    api.post(`/leads/${leadId}/stage`, { to_status: toStatus, reason }).then((r) => r.data),
  stageHistory: (leadId) => api.get(`/leads/${leadId}/stage-history`).then((r) => r.data),
}

export const authApi = {
  status:        ()        => api.get('/auth/status').then((r) => r.data),
  unlock:        (password) => api.post('/auth/unlock', { password }).then((r) => r.data),
  setPassword:   (password, currentPassword) =>
    api.post('/auth/set-password', { password, current_password: currentPassword }).then((r) => r.data),
  clearPassword: (password) => api.post('/auth/clear-password', { password }).then((r) => r.data),
  handoff:       (token)   => api.post('/auth/handoff', { token }).then((r) => r.data),
}

export const followupsApi = {
  pending: ()            => api.get('/followups/pending').then((r) => r.data),
  history: (params = {}) => api.get('/followups/history', { params }).then((r) => r.data),
  run:     ()            => api.post('/followups/run').then((r) => r.data),
}

// Phase 1 — Universal Lead Discovery (Quick Search / Discovery Planner)
export const discoveryApi = {
  search:  (payload) => api.post('/discovery/search', payload).then((r) => r.data),
  status:  (runId)   => api.get(`/discovery/search/${runId}`).then((r) => r.data),
  results: (runId)   => api.get(`/discovery/search/${runId}/results`).then((r) => r.data),
  cancel:  (runId)   => api.post(`/discovery/search/${runId}/cancel`).then((r) => r.data),
  // Reconnect to an in-flight (or just-finished) run after a navigation/refresh.
  active:  ()        => api.get('/discovery/search/active').then((r) => r.data),
}

// Client portal — the owner's side (clients, their requests, delivery).
export const clientsApi = {
  setup:         ()             => api.get('/clients/setup').then((r) => r.data),
  add:           (data)         => api.post('/clients', data).then((r) => r.data),
  update:        (id, data)     => api.patch(`/clients/${id}`, data).then((r) => r.data),
  settings:      ()             => api.get('/clients/portal-settings').then((r) => r.data),
  saveSettings:  (data)         => api.put('/clients/portal-settings', data).then((r) => r.data),
  testEmail:     (to)           => api.post('/clients/portal-settings/test-email', { to }).then((r) => r.data),
  list:          ()             => api.get('/clients').then((r) => r.data),
  setStatus:     (id, status)   => api.patch(`/clients/${id}`, { status }).then((r) => r.data),
  workspace:     (id, action, confirmEmail) => api.post(`/clients/${id}/workspace`, { action, confirm_email: confirmEmail }).then((r) => r.data),
  release:       ()             => api.get('/clients/release').then((r) => r.data),
  publish:       ()             => api.post('/clients/release/publish').then((r) => r.data),
}

// WhatsApp Campaigns (owner only) — backend/routers/whatsapp.py
export const whatsappApi = {
  status:       ()              => api.get('/whatsapp/status').then((r) => r.data),
  connect:      ()              => api.post('/whatsapp/session/start').then((r) => r.data),
  unlink:       ()              => api.post('/whatsapp/session/logout').then((r) => r.data),
  image:        (which)         => api.get(`/whatsapp/${which}.png`, { responseType: 'blob', params: { t: Date.now() } }).then((r) => r.data),
  activity:     ()              => api.get('/whatsapp/activity').then((r) => r.data.activity),
  conversation: (leadId)        => api.get(`/whatsapp/conversation/${leadId}`).then((r) => r.data.messages),
  chats:        ()              => api.get('/whatsapp/chats').then((r) => r.data.chats),
  saveSettings: (data)          => api.put('/whatsapp/settings', data).then((r) => r.data),
  audience:     (q)             => api.post('/whatsapp/audience', q).then((r) => r.data),
  uploadContacts: (file, countryCode, save) => {
    const fd = new FormData()
    fd.append('file', file); fd.append('country_code', countryCode || ''); fd.append('save', save ? 'true' : 'false')
    return api.post('/whatsapp/contacts', fd).then((r) => r.data)
  },
  campaigns:    ()              => api.get('/whatsapp/campaigns').then((r) => r.data.campaigns),
  meta:         ()              => api.get('/whatsapp/meta').then((r) => r.data),
  saveMeta:     (data)          => api.put('/whatsapp/meta', data).then((r) => r.data),
  testMeta:     ()              => api.post('/whatsapp/meta/test').then((r) => r.data),
  metaTemplates: ()             => api.get('/whatsapp/meta/templates').then((r) => r.data.templates),
  create:       (data)          => api.post('/whatsapp/campaigns', data).then((r) => r.data),
  action:       (id, action)    => api.post(`/whatsapp/campaigns/${id}/${action}`).then((r) => r.data),
}

// Social media automation — backend/routers/social.py
export const socialApi = {
  accounts:   ()            => api.get('/social/accounts').then((r) => r.data.accounts),
  discover:   (token)       => api.post('/social/accounts/meta/discover', { token }).then((r) => r.data.found),
  connect:    (token, pick) => api.post('/social/accounts/meta', { token, pick }).then((r) => r.data.accounts),
  check:      (id)          => api.post(`/social/accounts/${id}/check`).then((r) => r.data),
  remove:     (id)          => api.delete(`/social/accounts/${id}`),
  compose:    (data)        => api.post('/social/compose', data, { timeout: 300000 }).then((r) => r.data.captions),
  upload:     (file)        => { const fd = new FormData(); fd.append('file', file); return api.post('/social/media', fd).then((r) => r.data.media_name) },
  posts:      ()            => api.get('/social/posts').then((r) => r.data),
  create:     (data)        => api.post('/social/posts', data).then((r) => r.data),
  update:     (id, data)    => api.put(`/social/posts/${id}`, data).then((r) => r.data),
  schedule:   (id, when)    => api.post(`/social/posts/${id}/schedule`, { when }).then((r) => r.data),
  publish:    (id)          => api.post(`/social/posts/${id}/publish`, null, { timeout: 180000 }).then((r) => r.data),
  unschedule: (id)          => api.post(`/social/posts/${id}/unschedule`).then((r) => r.data),
  remove_post: (id)         => api.delete(`/social/posts/${id}`),
  activity:   ()            => api.get('/social/activity').then((r) => r.data.activity),
  connectLinkedin: (data)   => api.post('/social/accounts/linkedin', data).then((r) => r.data.accounts),
  connectX:   (data)        => api.post('/social/accounts/x', data).then((r) => r.data.accounts),
  addBrowser: (data)        => api.post('/social/accounts/browser', data).then((r) => r.data),
  browserOpen: (id)         => api.post(`/social/accounts/${id}/browser/open`, null, { timeout: 60000 }).then((r) => r.data),
  browserScreen: (id)       => api.get(`/social/accounts/${id}/browser/screen`, { responseType: 'blob' }).then((r) => r.data),
  browserAct: (id, action)  => api.post(`/social/accounts/${id}/browser/act`, action, { timeout: 60000 }).then((r) => r.data),
  browserDone: (id)         => api.post(`/social/accounts/${id}/browser/done`, null, { timeout: 60000 }).then((r) => r.data),
  inbox:      ()            => api.get('/social/inbox').then((r) => r.data),
  inboxSettings: (patch)    => api.put('/social/inbox/settings', patch).then((r) => r.data),
  inboxSync:  ()            => api.post('/social/inbox/sync', null, { timeout: 600000 }).then((r) => r.data),
  thread:     (id)          => api.get(`/social/inbox/${id}`).then((r) => r.data.messages),
  draft:      (id)          => api.post(`/social/inbox/${id}/draft`, null, { timeout: 300000 }).then((r) => r.data.text),
  reply:      (id, text)    => api.post(`/social/inbox/${id}/reply`, { text }).then((r) => r.data),
  mark:       (id, status)  => api.post(`/social/inbox/${id}/mark`, { status }).then((r) => r.data),
}

// Find leads runs — chain collect -> deep research -> draft outreach over one
// set of leads. Drafts only: sending stays in AI Lab.
export const leadRunsApi = {
  start:   (payload) => api.post('/lead-runs', payload).then((r) => r.data),
  list:    (limit = 10) => api.get('/lead-runs', { params: { limit } }).then((r) => r.data),
  get:     (id) => api.get(`/lead-runs/${id}`).then((r) => r.data),
  results: (id) => api.get(`/lead-runs/${id}/results`).then((r) => r.data),
  cancel:  (id) => api.post(`/lead-runs/${id}/cancel`).then((r) => r.data),
}

// Browser Research Agent — iterative LLM+Playwright deep research
export const researchAgentApi = {
  start:   (payload)   => api.post('/research-agent/start', payload).then((r) => r.data),
  status:  (sessionId) => api.get(`/research-agent/${sessionId}`).then((r) => r.data),
  results: (sessionId) => api.get(`/research-agent/${sessionId}/results`).then((r) => r.data),
  cancel:  (sessionId) => api.post(`/research-agent/${sessionId}/cancel`).then((r) => r.data),
  resume:  (sessionId) => api.post(`/research-agent/${sessionId}/resume`).then((r) => r.data),
  active:  ()          => api.get('/research-agent/active').then((r) => r.data),
  sessions: (params = {}) => api.get('/research-agent/sessions', { params }).then((r) => r.data),
  titles:  (niche = '') => api.get('/research-agent/titles', { params: { niche } }).then((r) => r.data),
  // Full researched dataset (business + management + evidence + statuses) as CSV.
  exportCsv: (sessionId) =>
    api.get(`/research-agent/${sessionId}/results.csv`, { responseType: 'blob' }).then((r) => r.data),
}

// Lead Search Automation — bulk niche×location scheduled discovery
export const automationApi = {
  previewImport: (formData) =>
    api.post('/automation/import/preview', formData, { headers: { 'Content-Type': 'multipart/form-data' } }).then((r) => r.data),
  confirmImport: (payload) => api.post('/automation/import/confirm', payload).then((r) => r.data),
  status:        ()        => api.get('/automation/status').then((r) => r.data),
  runs:          (limit)   => api.get('/automation/runs', { params: { limit } }).then((r) => r.data),
  queue:         (params)  => api.get('/automation/queue', { params }).then((r) => r.data),
  log:           (limit)   => api.get('/automation/log', { params: { limit } }).then((r) => r.data),
  saveSettings:  (payload) => api.put('/automation/settings', payload).then((r) => r.data),
  testSearch:    ()        => api.post('/automation/test-search').then((r) => r.data),
  start:         ()        => api.post('/automation/start').then((r) => r.data),
  pause:         ()        => api.post('/automation/pause').then((r) => r.data),
  resume:        ()        => api.post('/automation/resume').then((r) => r.data),
  stop:          ()        => api.post('/automation/stop').then((r) => r.data),
  reset:         ()        => api.post('/automation/reset', null, { params: { confirm: true } }).then((r) => r.data),
}

// Email Campaigns (PopupGenix) — feature-flagged (email_campaigns_enabled).
// TEST_MODE is forced on by the backend; the native email_sender is the only
// production sender. Endpoints return 503 while the feature flag is off.
export const emailCampaignsApi = {
  list:        ()                 => api.get('/email-campaigns').then((r) => r.data),
  get:         (id)              => api.get(`/email-campaigns/${id}`).then((r) => r.data),
  create:      (payload)         => api.post('/email-campaigns', payload).then((r) => r.data),
  patch:       (id, fields)      => api.patch(`/email-campaigns/${id}`, fields).then((r) => r.data),
  importLeads: (id, file)        => {
    const form = new FormData()
    form.append('file', file)
    return api.post(`/email-campaigns/${id}/leads/import`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }).then((r) => r.data)
  },
  leads:       (id, params = {}) => api.get(`/email-campaigns/${id}/leads`, { params }).then((r) => r.data),
  uploadAttachment: (id, file)  => {
    const form = new FormData()
    form.append('file', file)
    return api.post(`/email-campaigns/${id}/attachment`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    }).then((r) => r.data)
  },
  deleteAttachment: (id)        => api.delete(`/email-campaigns/${id}/attachment`).then((r) => r.data),
  prepare:     (id)             => api.post(`/email-campaigns/${id}/prepare`).then((r) => r.data),
  markReady:   (id)             => api.post(`/email-campaigns/${id}/ready`).then((r) => r.data),
  start:       (id, key)        => api.post(`/email-campaigns/${id}/start`, { idempotency_key: key }).then((r) => r.data),
  pause:       (id)             => api.post(`/email-campaigns/${id}/pause`).then((r) => r.data),
  resume:      (id, key)        => api.post(`/email-campaigns/${id}/resume`, { idempotency_key: key }).then((r) => r.data),
  stats:       (id)             => api.get(`/email-campaigns/${id}/stats`).then((r) => r.data),
  activity:    (id, limit = 100) => api.get(`/email-campaigns/${id}/activity`, { params: { limit } }).then((r) => r.data),
  n8nStatus:   ()               => api.get('/email-campaigns/n8n/status').then((r) => r.data),
  createFromSearch: (payload) =>
    api.post('/email-campaigns/from-search', payload).then((r) => r.data),
}

// Sender Profiles (Checkpoint 4) — which authorized account a campaign sends
// from. Responses carry only safe metadata (never passwords / OAuth tokens).
export const emailSendersApi = {
  list:         ()              => api.get('/email-senders').then((r) => r.data),
  get:          (id)            => api.get(`/email-senders/${id}`).then((r) => r.data),
  createSmtp:   (payload)       => api.post('/email-senders', payload).then((r) => r.data),
  patch:        (id, fields)    => api.patch(`/email-senders/${id}`, fields).then((r) => r.data),
  remove:       (id)            => api.delete(`/email-senders/${id}`).then((r) => r.data),
  test:         (id)            => api.post(`/email-senders/${id}/test`).then((r) => r.data),
  disconnect:   (id)            => api.post(`/email-senders/${id}/disconnect`).then((r) => r.data),
  setDefault:   (id)            => api.post(`/email-senders/${id}/default`).then((r) => r.data),
  gmailConfigStatus: ()         => api.get('/email-senders/gmail/config-status').then((r) => r.data),
  gmailConnect: ()              => api.get('/email-senders/gmail/connect').then((r) => r.data),
}

export default api
