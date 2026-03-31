const BASE = ''

export class ApiError extends Error {
  constructor(status, message) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

export async function apiFetch(path, options = {}) {
  const { json, ...rest } = options
  const headers = { ...rest.headers }

  if (json !== undefined) {
    headers['Content-Type'] = 'application/json'
    rest.body = JSON.stringify(json)
  }

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

export const api = {
  get:    (path)           => apiFetch(path),
  post:   (path, json)     => apiFetch(path, { method: 'POST',   json }),
  patch:  (path, json)     => apiFetch(path, { method: 'PATCH',  json }),
  delete: (path)           => apiFetch(path, { method: 'DELETE' }),
  // For multipart/form-data — do NOT set Content-Type; browser sets boundary automatically
  upload: (path, formData) => apiFetch(path, { method: 'POST', body: formData }),
}
