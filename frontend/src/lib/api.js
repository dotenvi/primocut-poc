const base = '/api'

async function req(method, path, body) {
  const r = await fetch(base + path, {
    method,
    headers: body ? { 'content-type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) {
    let detail = r.statusText
    try { detail = (await r.json()).detail || detail } catch (_) {}
    throw new Error(detail)
  }
  return r.status === 204 ? null : r.json()
}

export const api = {
  submit: (url) => req('POST', '/videos', { url }),
  list: () => req('GET', '/videos'),
  detail: (id) => req('GET', `/videos/${id}`),
  report: (id) => req('GET', `/videos/${id}/report`),
  generateTopics: (id) => req('POST', `/videos/${id}/topics`),
  reenqueue: (id) => req('POST', `/videos/${id}/reenqueue`),
  stop: (id) => req('POST', `/videos/${id}/stop`),
  remove: (id) => req('DELETE', `/videos/${id}`),
}

export const ACTIVE = ['queued', 'downloading', 'transcribing', 'analyzing']
export const isActive = (status) => ACTIVE.includes(status)
