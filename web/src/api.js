// In production VITE_API_BASE points at the Render web service. Locally it is
// empty and Vite's dev proxy forwards /api to 127.0.0.1:8000.
const BASE = (import.meta.env.VITE_API_BASE || '').replace(/\/$/, '')

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.status = status
  }
}

export async function get(path, params = {}) {
  const url = new URL(`${BASE}/api${path}`, window.location.origin)
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') {
      url.searchParams.set(key, value)
    }
  }

  const response = await fetch(url, { headers: { Accept: 'application/json' } })
  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try {
      const body = await response.json()
      if (body?.detail) detail = body.detail
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(detail, response.status)
  }
  return response.json()
}

/**
 * Poll /api/health until the server answers.
 *
 * Render's free tier spins a service down after 15 minutes with no traffic and
 * takes roughly a minute to wake. Without this, the first visitor gets a blank
 * page and leaves before the server is up — which is the whole audience for
 * this project. Waiting is fine; a blank tab is not.
 */
export async function waitForServer({ onAttempt, timeoutMs = 90000 } = {}) {
  const started = Date.now()
  let attempt = 0

  while (Date.now() - started < timeoutMs) {
    attempt += 1
    onAttempt?.(attempt, Math.round((Date.now() - started) / 1000))
    try {
      const health = await get('/health')
      if (health.status === 'ok') return true
    } catch {
      /* still asleep */
    }
    await new Promise((r) => setTimeout(r, 2500))
  }
  return false
}
