// Hash-based routing. Every destination is a real URL (linkable, and the browser
// back/forward buttons work); nothing about the current project lives in hidden
// component state — it is always in the path. Chosen over the History API so the
// FastAPI static server needs no deep-link catch-all.
//
// Routes:
//   #/                          → radar (cross-project)
//   #/activity                  → activity log
//   #/p/{pid}/lives             → lives list
//   #/p/{pid}/lives/{lid}       → cut room
//   #/p/{pid}/niche[/{tab}]     → niche, tab ∈ patterns|outliers|channels
//   #/p/{pid}/niche/channels/{cid}             → channel drill-down
//   #/p/{pid}/niche/channels/{cid}/videos/{vid} → competitor video detail
//   #/p/{pid}/settings          → own channels + read-only bands
// The competitor video carries its channel in the path (the endpoint is
// /channels/{cid}/videos/{vid}), so a pasted URL resolves on a cold load.

const NICHE_TABS = ['patterns', 'outliers', 'channels']

export function parseRoute(hash) {
  const path = (hash || '').replace(/^#/, '') || '/'
  const seg = path.split('/').filter(Boolean) // '/p/1/lives' → ['p','1','lives']

  if (seg.length === 0) return { view: 'radar' }
  if (seg[0] === 'activity') return { view: 'activity' }

  if (seg[0] === 'p' && seg[1]) {
    const pid = seg[1]
    const section = seg[2]
    if (section === 'lives') {
      return seg[3] ? { view: 'cutroom', pid, lid: seg[3] } : { view: 'lives', pid }
    }
    if (section === 'settings') return { view: 'settings', pid }
    if (section === 'niche') {
      if (seg[3] === 'channels' && seg[4]) {
        if (seg[5] === 'videos' && seg[6]) {
          return { view: 'video', pid, cid: seg[4], vid: seg[6] }
        }
        return { view: 'channel', pid, cid: seg[4] }
      }
      const tab = NICHE_TABS.includes(seg[3]) ? seg[3] : 'patterns'
      return { view: 'niche', pid, tab }
    }
    // Bare project → its lives.
    return { view: 'lives', pid }
  }
  return { view: 'radar' }
}

export const href = {
  radar: () => '#/',
  activity: () => '#/activity',
  lives: (pid) => `#/p/${pid}/lives`,
  cutroom: (pid, lid) => `#/p/${pid}/lives/${lid}`,
  niche: (pid, tab) => `#/p/${pid}/niche${tab && tab !== 'patterns' ? `/${tab}` : ''}`,
  video: (pid, cid, vid) => `#/p/${pid}/niche/channels/${cid}/videos/${vid}`,
  channel: (pid, cid) => `#/p/${pid}/niche/channels/${cid}`,
  settings: (pid) => `#/p/${pid}/settings`,
}

export const goto = (hashUrl) => {
  location.hash = hashUrl
}
