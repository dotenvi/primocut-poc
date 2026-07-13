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

// Derive each step's state from the current stage/status.
// state ∈ 'done' | 'running' | 'failed' | 'pending'
export function computeSteps(stage, status) {
  const s = (stage || '').toLowerCase()
  let current = STEPS.findIndex((st) => s.includes(st.match))

  return STEPS.map((st, i) => {
    let state
    if (status === 'done') {
      state = 'done'
    } else if (current < 0) {
      state = 'pending' // still queued
    } else if (i < current) {
      state = 'done'
    } else if (i === current) {
      state = status === 'error' || status === 'stopped' ? 'failed' : 'running'
    } else {
      state = 'pending'
    }
    return { ...st, state }
  })
}
