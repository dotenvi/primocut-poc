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

const qs = (params) => {
  const p = new URLSearchParams()
  for (const [k, v] of Object.entries(params || {})) {
    if (v !== undefined && v !== null && v !== '') p.set(k, v)
  }
  const s = p.toString()
  return s ? `?${s}` : ''
}

export const api = {
  // Lives — CONTAINED in a project (the project comes from the route, never
  // picked twice). These replace the old top-level /videos endpoints.
  submit: (pid, url) => req('POST', `/projects/${pid}/lives`, { url }),
  lives: (pid) => req('GET', `/projects/${pid}/lives`),
  live: (pid, lid) => req('GET', `/projects/${pid}/lives/${lid}`),
  report: (pid, lid) => req('GET', `/projects/${pid}/lives/${lid}/report`),
  generateTopics: (pid, lid) => req('POST', `/projects/${pid}/lives/${lid}/topics`),
  reenqueue: (pid, lid) => req('POST', `/projects/${pid}/lives/${lid}/reenqueue`),
  stop: (pid, lid) => req('POST', `/projects/${pid}/lives/${lid}/stop`),
  removeLive: (pid, lid) => req('DELETE', `/projects/${pid}/lives/${lid}`),

  // Radar — global event log. Success is silent; failure is loud.
  events: (params) => req('GET', `/events${qs({ category: 'niche', ...params })}`),
  alerts: () => req('GET', '/alerts'),
  activity: (params) => req('GET', `/activity${qs(params)}`),
  markSeen: (body) => req('POST', '/events/seen', body),

  // Projects (niches).
  projects: () => req('GET', '/projects'),
  project: (pid) => req('GET', `/projects/${pid}`),
  createProject: (body) => req('POST', '/projects', body),
  patchProject: (pid, body) => req('PATCH', `/projects/${pid}`, body),
  deleteProject: (pid) => req('DELETE', `/projects/${pid}`),

  // Competitor channels within a project (the Niche › Channels tab).
  channels: (pid) => req('GET', `/projects/${pid}/channels`),
  addChannel: (pid, url) => req('POST', `/projects/${pid}/channels`, { url }),
  detachChannel: (pid, cid) => req('DELETE', `/projects/${pid}/channels/${cid}`),

  // Own channels within a project (the clip destinations — Settings).
  ownChannels: (pid) => req('GET', `/projects/${pid}/own-channels`),
  addOwnChannel: (pid, body) => req('POST', `/projects/${pid}/own-channels`, body),
  deleteOwnChannel: (pid, id) => req('DELETE', `/projects/${pid}/own-channels/${id}`),

  // Niche intelligence.
  projectDashboard: (pid) => req('GET', `/projects/${pid}/dashboard`),
  channelDashboard: (cid) => req('GET', `/channels/${cid}/dashboard`),
  competitorVideo: (cid, vid) => req('GET', `/channels/${cid}/videos/${vid}`),

  // Channel-spread distribution — same payload for the detail strip and the
  // Radar cards. One call per page, or one batched call per Radar page.
  distribution: (cid, fmt) => req('GET', `/channels/${cid}/distribution${qs({ format: fmt })}`),
  distributionBatch: (cids, fmt) =>
    req('GET', `/channels/distribution${qs({ channel_ids: cids.join(','), format: fmt })}`),

  // Opt-in AI enrichment (costs an LLM call): kind is 'thumbnail', 'title' or
  // 'comments'. `regenerate` (comments only) re-fetches them before synthesising.
  enrichVideo: (cid, vid, kind, regenerate = false) =>
    req('POST', `/channels/${cid}/videos/${vid}/enrich`, { kind, regenerate }),
  enrichProject: (pid, kind) => req('POST', `/projects/${pid}/enrich`, { kind }),
  // How many videos an enrich would newly process vs. already cached, so the
  // button shows the cost before the click.
  enrichPreview: (pid, kind) => req('GET', `/projects/${pid}/enrich/preview${qs({ kind })}`),
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

// The YouTube Data API free-tier daily budget — the denominator of the sidebar
// quota readout. The numerator (units spent) comes from GET /api/activity.
export const DAILY_QUOTA = 10000
