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

  // Cut room (Phase 3): the decision bench — cuts, items (topic or manual range),
  // transactional reorder, and per-live topic triage. No rendering is triggered.
  cuts: (pid, lid) => req('GET', `/projects/${pid}/lives/${lid}/cuts`),
  createCut: (pid, lid, name) => req('POST', `/projects/${pid}/lives/${lid}/cuts`, { name }),
  bench: (pid, lid, cid) => req('GET', `/projects/${pid}/lives/${lid}/cuts/${cid}`),
  renameCut: (pid, lid, cid, name) =>
    req('PATCH', `/projects/${pid}/lives/${lid}/cuts/${cid}`, { name }),
  deleteCut: (pid, lid, cid) => req('DELETE', `/projects/${pid}/lives/${lid}/cuts/${cid}`),
  addCutItem: (pid, lid, cid, body) =>
    req('POST', `/projects/${pid}/lives/${lid}/cuts/${cid}/items`, body),
  removeCutItem: (pid, lid, cid, iid) =>
    req('DELETE', `/projects/${pid}/lives/${lid}/cuts/${cid}/items/${iid}`),
  reorderCutItems: (pid, lid, cid, itemIds) =>
    req('PATCH', `/projects/${pid}/lives/${lid}/cuts/${cid}/items`, { item_ids: itemIds }),
  setTriage: (pid, lid, tid, state) =>
    req('PATCH', `/projects/${pid}/lives/${lid}/topics/${tid}/triage`, { state }),

  // Rendering (Phase 3, step 2): background per-item clip render on the existing
  // queue. `body` = {} for all items, {item_id} for one; {regenerate}/{fast_copy}
  // as options. Progress is polled per clip via the bench (item.clip) or listClips.
  render: (pid, lid, cid, body = {}) =>
    req('POST', `/projects/${pid}/lives/${lid}/cuts/${cid}/render`, body),
  listClips: (pid, lid, cid) => req('GET', `/projects/${pid}/lives/${lid}/cuts/${cid}/clips`),
  deleteClip: (pid, lid, cid, clipId) =>
    req('DELETE', `/projects/${pid}/lives/${lid}/cuts/${cid}/clips/${clipId}`),

  // Export (Phase 3, later step): package the cut's clips + SRT + master +
  // manifests into a zip, in the background. `body` = {render_missing, skip_master,
  // upscale_1440p}. Polled via the bench (`export`) or exportStatus.
  export: (pid, lid, cid, body = {}) =>
    req('POST', `/projects/${pid}/lives/${lid}/cuts/${cid}/export`, body),
  exportStatus: (pid, lid, cid) =>
    req('GET', `/projects/${pid}/lives/${lid}/cuts/${cid}/export`),

  // AI packaging (Phase 3, opt-in — each LLM pass costs money; frame EXTRACTION is
  // free/deterministic). `context` is the read-only niche patterns kit; the rest
  // run in the background, polled per item via the bench (item.packaging).
  packagingContext: (pid, lid) =>
    req('GET', `/projects/${pid}/lives/${lid}/packaging/context`),
  genTitles: (pid, lid, cid, body = {}) =>
    req('POST', `/projects/${pid}/lives/${lid}/cuts/${cid}/packaging/titles`, body),
  genBrief: (pid, lid, cid, body = {}) =>
    req('POST', `/projects/${pid}/lives/${lid}/cuts/${cid}/packaging/brief`, body),
  extractFrames: (pid, lid, cid, body = {}) =>
    req('POST', `/projects/${pid}/lives/${lid}/cuts/${cid}/packaging/frames`, body),
  rankFrames: (pid, lid, cid, body = {}) =>
    req('POST', `/projects/${pid}/lives/${lid}/cuts/${cid}/packaging/frames/rank`, body),
  matchAudience: (pid, lid, body = {}) =>
    req('POST', `/projects/${pid}/lives/${lid}/audience-match`, body),

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

// A clip is still being produced (worth polling) until it is done or errored.
const CLIP_ACTIVE = ['queued', 'downloading_section', 'cutting']
export const clipActive = (clip) => !!clip && CLIP_ACTIVE.includes(clip.state)

// An export is still being produced (worth polling) until it is done or errored.
const EXPORT_ACTIVE = ['queued', 'packaging']
export const exportActive = (exp) => !!exp && EXPORT_ACTIVE.includes(exp.state)

// A packaging row (title/thumb_brief/frame_ranking) is worth polling while its
// LLM pass is in flight. `item.packaging[kind]` is null until the first run.
export const packagingActive = (row) => !!row && row.state === 'running'
// Any of a cut's items still has a packaging pass running → keep polling the bench.
export const cutPackagingActive = (items) =>
  (items || []).some((it) => ['title', 'thumb_brief', 'frame_ranking']
    .some((k) => packagingActive(it.packaging && it.packaging[k])))

export const ACTIVE = ['queued', 'downloading', 'transcribing', 'analyzing']
export const isActive = (status) => ACTIVE.includes(status)

// The YouTube Data API free-tier daily budget — the denominator of the sidebar
// quota readout. The numerator (units spent) comes from GET /api/activity.
export const DAILY_QUOTA = 10000
