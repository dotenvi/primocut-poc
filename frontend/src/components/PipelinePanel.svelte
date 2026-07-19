<script>
  // The pipeline section, moved out of the Cut room and onto the Lives list:
  // the stage rail plus the per-step log (canonical execution order, with each
  // step's settled state + duration from the shared stage truth) and a rerun
  // control. Fed the live's /report payload by the parent.
  import { createEventDispatcher } from 'svelte'
  import { api } from '../lib/api.js'
  import { formatDuration } from '../lib/format.js'
  import { computeSteps } from '../lib/steps.js'
  import PipelineRail from './PipelineRail.svelte'

  export let data // the live's /report payload
  export let pid
  export let lid

  const dispatch = createEventDispatcher()

  // Per-state status chip for the log: a round icon plus its word. Kept in
  // lock-step with computeSteps' state vocabulary so the log and the rail never
  // disagree.
  const STATUS = {
    done:    { word: 'complete',   wcls: 'text-ok',    ring: 'bg-ok text-white',                           glyph: '✓' },
    failed:  { word: 'failed',     wcls: 'text-err',   ring: 'bg-err text-white',                          glyph: '✕' },
    running: { word: 'processing', wcls: 'text-accent',ring: 'bg-accent text-accent-on animate-pulse',     glyph: '' },
    pending: { word: 'queued',     wcls: 'text-ink-3', ring: 'border border-line',                         glyph: '' },
    empty:   { word: 'ignored',    wcls: 'text-ink-3', ring: 'bg-line-strong',                             glyph: '' },
    skipped: { word: 'skipped',    wcls: 'text-ink-3', ring: 'border border-dashed border-line-strong text-ink-3', glyph: '/' },
  }

  let busy = false

  $: completed = data
    ? {
        metadata: !!data.video,
        audio: data.transcript.length > 0,
        transcription: data.transcript.length > 0,
        chat: !!data.chat_signal,
        topics: data.topics.length > 0,
      }
    : {}

  // The log, in canonical pipeline execution order (never raw history order),
  // with each step's settled state + duration from the shared stage truth.
  $: logSteps = data
    ? computeSteps(
        data.job ? data.job.stage : '',
        data.video.status,
        completed,
        data.job ? data.job.stage_history : []
      )
    : []

  async function rerun() {
    if (busy) return
    busy = true
    try {
      await api.reenqueue(pid, lid)
      dispatch('rerun')
    } catch (e) {
      dispatch('error', e.message)
    } finally {
      busy = false
    }
  }
</script>

<div class="card py-2.5">
  <div class="flex items-center justify-between gap-3">
    <div class="flex items-center gap-3">
      <span class="lbl">Pipeline</span>
      <PipelineRail
        stage={data.job ? data.job.stage : ''}
        status={data.video.status}
        stageHistory={data.job ? data.job.stage_history : []}
        {completed}
      />
    </div>
    <div class="flex gap-1.5">
      <button class="btn btn-xs" on:click={rerun} disabled={busy}>Rerun stages</button>
    </div>
  </div>
  <div class="mt-2.5 border-t border-line pt-2.5">
    {#if data.warnings && data.warnings.length}
      {#each data.warnings as w}<div class="m text-err mb-1.5">⚠ {w}</div>{/each}
    {/if}
    {#each logSteps as s (s.key)}
      {@const st = STATUS[s.state] || STATUS.pending}
      <div class="flex items-center justify-between py-1.5 border-t border-line first:border-t-0">
        <span class="m text-ink-2">{s.label}</span>
        <div class="flex items-center gap-2.5">
          <span class="m text-ink-3 tabular-nums">{formatDuration(s.durationSec)}</span>
          <span class="font-mono text-[10px] {st.wcls} w-[70px] text-right">{st.word}</span>
          <span
            class="inline-flex items-center justify-center w-4 h-4 rounded-full text-[9px] leading-none font-bold shrink-0 {st.ring}"
            title={st.word}
          >{st.glyph}</span>
        </div>
      </div>
    {/each}
  </div>
</div>
