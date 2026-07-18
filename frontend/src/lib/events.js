// Presentation mapping for Radar / Activity event rows. The band colours follow
// the prototype's heat scale (p99 > p97 > p90); non-band niche events get their
// own hue (packaging = violet, resurgence = accent), and ops failures read red.
import { compactNumber, multiplier } from './format.js'

const BAND = {
  exceptional: { abbr: 'p99', cls: 'bg-heat-bg99 text-heat-99 border-heat-ln99' },
  strong: { abbr: 'p97', cls: 'bg-heat-bg97 text-heat-97 border-heat-ln97' },
  relevant: { abbr: 'p90', cls: 'bg-heat-bg90 text-heat-90 border-heat-ln90' },
}

const NEUTRAL = 'bg-card2 text-ink-2 border-line-strong'

// The coloured tag for an event card: band-scaled for a crossing, hued per type
// otherwise. `{ text, cls, abbr }` — `abbr` is the short form for compact rows.
export function eventTag(ev) {
  const p = ev.payload || {}
  if (ev.type === 'outlier_crossed') {
    const b = BAND[p.band] || BAND.relevant
    const suffix = p.provisional ? ' provisional' : ` ${p.band}`
    return { text: `${b.abbr}${suffix}`, cls: b.cls, abbr: b.abbr }
  }
  if (ev.type === 'packaging_changed')
    return { text: 'packaging', cls: 'bg-viol-bg text-viol border-viol-line', abbr: 'pkg' }
  if (ev.type === 'resurgence')
    return { text: 'resurgence', cls: 'bg-accent-bg text-accent border-accent-line', abbr: '↑' }
  if (ev.type === 'backfill_done')
    return { text: 'backfill done', cls: NEUTRAL, abbr: 'backfill' }
  const text = ev.type.replace(/_/g, ' ')
  return { text, cls: NEUTRAL, abbr: text }
}

// The one-line metric under a card title.
export function eventMeta(ev) {
  const p = ev.payload || {}
  if (ev.type === 'outlier_crossed') {
    const parts = []
    if (p.views != null) parts.push(`${compactNumber(p.views)} views`)
    if (p.median_multiple != null) parts.push(`${multiplier(p.median_multiple)} median`)
    if (p.provisional) parts.push('provisional')
    return parts.join(' · ')
  }
  if (ev.type === 'packaging_changed') return `${p.field || 'packaging'} changed`
  if (ev.type === 'resurgence' && p.ratio != null)
    return `${multiplier(p.ratio)} its recent average`
  return ''
}

// The video format an event refers to (for the channel-spread strip). Defaults
// to long-form when the payload predates the `is_short` field.
export const eventFormat = (ev) => ((ev.payload || {}).is_short ? 'short' : 'long')
