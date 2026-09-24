import axios from 'axios'

// Website + client account API: talks only to /api/portal/* with the client's
// own session token (never the owner's). A 401 signs the client out.
const TOKEN_KEY = 'hom_portal_token'
export const getToken = () => { try { return localStorage.getItem(TOKEN_KEY) } catch { return null } }
export const setToken = (t) => { try { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY) } catch { /* private mode */ } }

const api = axios.create({ baseURL: '/api/portal', timeout: 30000 })
api.interceptors.request.use((cfg) => {
  const t = getToken()
  if (t) cfg.headers.Authorization = `Bearer ${t}`
  return cfg
})
api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401 && getToken() && !String(err.config?.url || '').startsWith('/auth/')) {
      setToken(null)
      window.dispatchEvent(new CustomEvent('portal:signed-out'))
    }
    const e = new Error(err.response?.data?.detail || 'Something went wrong. Please try again.')
    e.status = err.response?.status
    return Promise.reject(e)
  },
)

export const portalApi = {
  status: () => api.get('/status').then((r) => r.data),
  signup: (data) => api.post('/auth/signup', data).then((r) => r.data),
  login: (email, password) => api.post('/auth/login', { email, password }).then((r) => r.data),
  requestCode: (email, purpose = 'reset') => api.post('/auth/request-code', { email, purpose }).then((r) => r.data),
  setPassword: (password, currentPassword) => api.post('/me/password', { password, current_password: currentPassword }).then((r) => r.data),
  verify: (email, code) => api.post('/auth/verify', { email, code }).then((r) => r.data),
  signOut: () => api.post('/auth/sign-out').then((r) => r.data),
  me: () => api.get('/me').then((r) => r.data),
  updateMe: (data) => api.put('/me', data).then((r) => r.data),
  workspace: () => api.get('/workspace').then((r) => r.data),
  enter: () => api.post('/workspace/enter').then((r) => r.data),
}
