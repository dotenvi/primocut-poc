// Canonical pipeline steps (in execution order). `match` matches the `stage`
// text reported by the backend.
export const STEPS = [
  { key: 'metadata', label: 'Metadata', match: 'metadata' },
  { key: 'audio', label: 'Audio download', match: 'audio' },
  { key: 'transcription', label: 'Transcription', match: 'transcription' },
  { key: 'chat', label: 'Chat', match: 'chat' },
  { key: 'topics', label: 'Macro topics', match: 'topics' },
]

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
