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

// Derive each step's state from the current stage/status AND per-step evidence.
// state ∈ 'done' | 'running' | 'failed' | 'pending' | 'skipped'
//
// A step is only marked 'done' when there is positive confirmation that it
// produced its artifact (`completed[key] === true`) — never just because the
// pipeline finished. This way an optional/skipped step (e.g. topics not
// generated, or chat with no replay) is honestly shown as 'skipped' instead of
// falsely 'done'. `completed` is a map keyed by STEP.key → boolean, built by
// the caller from the report data.
export function computeSteps(stage, status, completed = {}) {
  const s = (stage || '').toLowerCase()
  const current = STEPS.findIndex((st) => s.includes(st.match))
  const active = ACTIVE_STATUSES.includes(status)

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
    return { ...st, state }
  })
}
