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
    return Promise.reject(new Error(msg))
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
  getIntelligence:  (leadId) => api.get(`/leads/${leadId}/intelligence`).then((r) => r.data),
  getPainPoints:    (leadId) => api.get(`/leads/${leadId}/pain-points`).then((r) => r.data),
  getOpportunities: (leadId) => api.get(`/leads/${leadId}/opportunities`).then((r) => r.data),
  getSolutions:     (leadId) => api.get(`/leads/${leadId}/solutions`).then((r) => r.data),
  getEvidence:      (leadId) => api.get(`/leads/${leadId}/evidence`).then((r) => r.data),
  analyzePainPoints:   (leadId) => api.post(`/leads/${leadId}/pain-points/analyze`).then((r) => r.data),
  analyzeOpportunities: (leadId) => api.post(`/leads/${leadId}/opportunities/analyze`).then((r) => r.data),
}

export const authApi = {
  status:        ()        => api.get('/auth/status').then((r) => r.data),
  unlock:        (password) => api.post('/auth/unlock', { password }).then((r) => r.data),
  setPassword:   (password, currentPassword) =>
    api.post('/auth/set-password', { password, current_password: currentPassword }).then((r) => r.data),
  clearPassword: (password) => api.post('/auth/clear-password', { password }).then((r) => r.data),
}

export const followupsApi = {
  pending: ()            => api.get('/followups/pending').then((r) => r.data),
  history: (params = {}) => api.get('/followups/history', { params }).then((r) => r.data),
  run:     ()            => api.post('/followups/run').then((r) => r.data),
}

export default api
