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

  // Phase 2 — competitor channel modeling.
  projects: () => req('GET', '/projects'),
  project: (pid) => req('GET', `/projects/${pid}`),
  createProject: (body) => req('POST', '/projects', body),
  patchProject: (pid, body) => req('PATCH', `/projects/${pid}`, body),
  deleteProject: (pid) => req('DELETE', `/projects/${pid}`),
  channels: (pid) => req('GET', `/projects/${pid}/channels`),
  addChannel: (pid, url) => req('POST', `/projects/${pid}/channels`, { url }),
  detachChannel: (pid, cid) => req('DELETE', `/projects/${pid}/channels/${cid}`),
  projectDashboard: (pid) => req('GET', `/projects/${pid}/dashboard`),
  channelDashboard: (cid) => req('GET', `/channels/${cid}/dashboard`),
  competitorVideo: (cid, vid) => req('GET', `/channels/${cid}/videos/${vid}`),
  // Opt-in AI enrichment (costs an LLM call): kind is 'thumbnail' or 'title'.
  enrichVideo: (cid, vid, kind) => req('POST', `/channels/${cid}/videos/${vid}/enrich`, { kind }),
  enrichProject: (pid, kind) => req('POST', `/projects/${pid}/enrich`, { kind }),
}

// Public YouTube watch URL (built client-side; the metrics payload carries only the id).
export const youtubeWatch = (id) => `https://www.youtube.com/watch?v=${id}`

// A channel's backfill is terminal (no longer worth polling) once it is done,
// errored, or was skipped (channel already known to the system).
const BACKFILL_ACTIVE = ['pending', 'queued', 'running']
export const backfillActive = (state) => BACKFILL_ACTIVE.includes(state)

// An enrichment row is worth polling only while its LLM call is in flight.
export const enrichActive = (attrs) => !!attrs && attrs.state === 'running'

export const ACTIVE = ['queued', 'downloading', 'transcribing', 'analyzing']
export const isActive = (status) => ACTIVE.includes(status)
