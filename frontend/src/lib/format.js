export function hms(seconds) {
  seconds = Math.floor(seconds || 0)
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  const pad = (n) => String(n).padStart(2, '0')
  return h ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`
}

// Compact human duration for pipeline stage timings, e.g. "34m 16s", "12s",
// "0s", "1h 2m". Returns '' for null/undefined (nothing to show).
export function formatDuration(seconds) {
  if (seconds == null) return ''
  seconds = Math.max(0, Math.round(seconds))
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  if (h) return `${h}h ${m}m`
  if (m) return `${m}m ${s}s`
  return `${s}s`
}

// Compact count, e.g. 1234 -> "1.2k", 3400000 -> "3.4M". '—' for null/undefined.
export function compactNumber(n) {
  if (n == null) return '—'
  const abs = Math.abs(n)
  if (abs >= 1e9) return (n / 1e9).toFixed(1).replace(/\.0$/, '') + 'B'
  if (abs >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, '') + 'M'
  if (abs >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, '') + 'k'
  return String(Math.round(n))
}

// A 0..1 rate as a percentage, e.g. 0.042 -> "4.2%". '—' for null/undefined.
export function pct(rate) {
  if (rate == null) return '—'
  return (rate * 100).toFixed(1).replace(/\.0$/, '') + '%'
}

// Views-per-hour label, e.g. 1234 -> "1.2k/h". '—' for null/undefined.
export function vphLabel(vph) {
  if (vph == null) return '—'
  return compactNumber(vph) + '/h'
}

// Multiplier label for outlier scores / ratios, e.g. 8.23 -> "8.2×". '—' for null.
export function multiplier(x) {
  if (x == null) return '—'
  return x.toFixed(1) + '×'
}

// ISO date (naive UTC) -> short local date, e.g. "2026-05-01". '' for null.
export function shortDate(iso) {
  if (!iso) return ''
  const d = new Date(iso.endsWith('Z') || /[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z')
  if (isNaN(d)) return ''
  return d.toISOString().slice(0, 10)
}

export const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
