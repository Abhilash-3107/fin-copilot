const BASE = '/api'
const TOKEN_KEY = 'fc_api_token'

export class ApiError extends Error {
  constructor(status, message) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

// Optional shared-secret token for a deliberate LAN bind (4.9). Persisted in
// localStorage and sent on every request; empty means no auth, the default.
export function getApiToken() {
  try { return localStorage.getItem(TOKEN_KEY) || '' } catch { return '' }
}
export function setApiToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch { /* storage unavailable; token just won't persist */ }
}

export async function apiFetch(path, options = {}) {
  const { json, ...rest } = options
  const headers = { ...rest.headers }

  if (json !== undefined) {
    headers['Content-Type'] = 'application/json'
    rest.body = JSON.stringify(json)
  }

  const token = getApiToken()
  if (token) headers['Authorization'] = `Bearer ${token}`

  const res = await fetch(BASE + path, { ...rest, headers })

  if (!res.ok) {
    const text = await res.text()
    let detail = text
    try {
      const parsed = JSON.parse(text)
      detail = parsed.detail ?? text
    } catch (_) {}
    throw new ApiError(res.status, detail)
  }

  const contentType = res.headers.get('content-type') ?? ''
  if (res.status === 204 || !contentType.includes('application/json')) {
    return null
  }
  return res.json()
}

// Fetch a file through the token-aware client and save it, so downloads work
// under token auth (a plain <a href> can't set the Authorization header).
export async function downloadFile(path, fallbackName = 'download') {
  const token = getApiToken()
  const headers = token ? { Authorization: `Bearer ${token}` } : {}
  const res = await fetch(BASE + path, { headers })
  if (!res.ok) throw new ApiError(res.status, await res.text())
  const disposition = res.headers.get('content-disposition') ?? ''
  const match = disposition.match(/filename=([^;]+)/)
  const name = match ? match[1].trim().replace(/"/g, '') : fallbackName
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export const api = {
  get:    (path)           => apiFetch(path),
  post:   (path, json)     => apiFetch(path, { method: 'POST',   json }),
  put:    (path, json)     => apiFetch(path, { method: 'PUT',    json }),
  patch:  (path, json)     => apiFetch(path, { method: 'PATCH',  json }),
  delete: (path)           => apiFetch(path, { method: 'DELETE' }),
  // For multipart/form-data — do NOT set Content-Type; browser sets boundary automatically
  upload: (path, formData) => apiFetch(path, { method: 'POST', body: formData }),
}

// Poll an existing job id until it finishes.
// onProgress receives the job row ({status, processed, total, ...}) on each poll.
// Resolves with the AutoAnnotateResult; rejects if the job fails.
//
// A failed poll is not a failed job: the worker keeps running server-side, so a
// transient error (a 5xx, a dropped connection) is retried. Only a 4xx (job
// gone, auth) or MAX_POLL_FAILURES consecutive errors give up.
export const MAX_POLL_FAILURES = 5

export async function pollAnnotationJob(jobId, onProgress, pollMs = 1000) {
  let failures = 0
  for (;;) {
    await new Promise(r => setTimeout(r, pollMs))
    let job
    try {
      job = await api.get(`/annotations/jobs/${jobId}`)
    } catch (err) {
      const clientError = err instanceof ApiError && err.status >= 400 && err.status < 500
      if (clientError || ++failures >= MAX_POLL_FAILURES) throw err
      continue
    }
    failures = 0
    onProgress?.(job)
    if (job.status === 'completed') return job.result
    if (job.status === 'failed') throw new ApiError(500, job.error || 'Annotation job failed')
  }
}

// Start a background auto-annotate job and poll until it finishes.
export async function runAnnotationJob(body, onProgress, pollMs = 1000) {
  const { job_id } = await api.post('/annotations/auto-annotate/jobs', body)
  return pollAnnotationJob(job_id, onProgress, pollMs)
}

// Return the currently in-flight annotation job, or null. Used to re-attach the
// progress card after a page reload (the server keeps working; React state was lost).
export async function getActiveAnnotationJob() {
  const jobs = await api.get('/annotations/jobs?active=1')
  return jobs[0] ?? null
}
