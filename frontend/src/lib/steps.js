// Etapas canônicas da pipeline (na ordem de execução). `match` casa com o
// texto do `stage` que o backend reporta.
export const STEPS = [
  { key: 'metadata', label: 'Metadados', match: 'metadados' },
  { key: 'audio', label: 'Download do áudio', match: 'download' },
  { key: 'transcription', label: 'Transcrição', match: 'transcri' },
  { key: 'chat', label: 'Chat', match: 'chat' },
  { key: 'topics', label: 'Tópicos macro', match: 'tópicos' },
  { key: 'moments', label: 'Momentos de corte', match: 'momentos' },
]

// Deriva o estado de cada etapa a partir do stage/status atuais.
// state ∈ 'done' | 'running' | 'failed' | 'pending'
export function computeSteps(stage, status) {
  const s = (stage || '').toLowerCase()
  let current = STEPS.findIndex((st) => s.includes(st.match))

  return STEPS.map((st, i) => {
    let state
    if (status === 'done') {
      state = 'done'
    } else if (current < 0) {
      state = 'pending' // ainda na fila
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
