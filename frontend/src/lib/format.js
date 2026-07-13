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
