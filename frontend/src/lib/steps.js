// Canonical pipeline steps (in execution order). `match` matches the `stage`
// text reported by the backend.
export const STEPS = [
  { key: 'metadata', label: 'Metadata', match: 'metadata' },
  { key: 'audio', label: 'Audio download', match: 'audio' },
  { key: 'transcription', label: 'Transcription', match: 'transcription' },
  { key: 'chat', label: 'Chat', match: 'chat' },
  { key: 'topics', label: 'Macro topics', match: 'topics' },
]

// Current step summary for a compact progress indicator: the active step's
// label plus an "N / total" counter. `index` is -1 while still queued.
export function currentStep(stage) {
  const s = (stage || '').toLowerCase()
  const idx = STEPS.findIndex((st) => s.includes(st.match))
  return {
    index: idx,
    number: idx < 0 ? 0 : idx + 1,
    total: STEPS.length,
    label: idx < 0 ? 'Queued' : STEPS[idx].label,
  }
}

const ACTIVE_STATUSES = ['queued', 'downloading', 'transcribing', 'analyzing']

// Map a backend stage label (e.g. "Analysis: topics") to a canonical STEP key,
// or null for terminal markers ("Done", "__end__") that bound a step but are
// not steps themselves.
function stageKey(label) {
  const s = (label || '').toLowerCase()
  const st = STEPS.find((x) => s.includes(x.match))
  return st ? st.key : null
}

// Derive per-step durations (seconds) from the job's `stage_history` — an
// ordered list of {stage, at} transitions. A step's duration is the gap to the
// next transition; the still-running step (last, non-terminal transition) runs
// up to `now` so it ticks live while polling. Returns a map STEP.key → seconds.
export function stepDurations(stageHistory = [], now = Date.now()) {
  const out = {}
  const tx = (stageHistory || [])
    .map((h) => ({ key: stageKey(h.stage), at: Date.parse(h.at) }))
    .filter((h) => !Number.isNaN(h.at))
  for (let i = 0; i < tx.length; i++) {
    if (!tx[i].key) continue // terminal marker, not a step
    const next = i + 1 < tx.length ? tx[i + 1].at : now
    out[tx[i].key] = Math.max(0, (next - tx[i].at) / 1000)
  }
  return out
}

// Derive each step's state from the current stage/status AND per-step evidence.
// state ∈ 'done' | 'running' | 'failed' | 'pending' | 'skipped'
//
// A step is only marked 'done' when there is positive confirmation that it
// produced its artifact (`completed[key] === true`) — never just because the
// pipeline finished. This way an optional/skipped step (e.g. topics not
// generated, or chat with no replay) is honestly shown as 'skipped' instead of
// falsely 'done'. `completed` is a map keyed by STEP.key → boolean, built by
// the caller from the report data.
export function computeSteps(stage, status, completed = {}, stageHistory = []) {
  const s = (stage || '').toLowerCase()
  const current = STEPS.findIndex((st) => s.includes(st.match))
  const active = ACTIVE_STATUSES.includes(status)
  const durations = stepDurations(stageHistory)

  return STEPS.map((st, i) => {
    let state
    if (completed[st.key]) {
      state = 'done' // confirmed: the artifact exists
    } else if (active && current >= 0 && i < current) {
      state = 'done' // already passed this step in the current run
    } else if (active && i === current) {
      state = 'running'
    } else if ((status === 'error' || status === 'stopped') && i === current) {
      state = 'failed'
    } else {
      // Not confirmed and not the active/failed step: upcoming while running,
      // otherwise it was skipped or never reached.
      state = active ? 'pending' : 'skipped'
    }
    const durationSec = durations[st.key]
    return { ...st, state, durationSec: durationSec == null ? null : durationSec }
  })
}
