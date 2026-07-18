<script>
  // The horizontal stage rail (lamps joined by hairlines) from the prototype.
  // State comes from the shared `computeSteps`, the single source of stage truth,
  // so the rail agrees with the report's own pipeline view.
  import { computeSteps } from '../lib/steps.js'

  export let stage = ''
  export let status = 'queued'
  export let completed = {}
  export let stageHistory = []

  const LAMP = {
    done: 'bg-ok',
    running: 'bg-accent shadow-[0_0_0_3px_theme(colors.accent.bg)]',
    failed: 'bg-err',
    pending: '',
    empty: '',
    skipped: '',
  }
  const WORD = {
    done: { text: 'complete', cls: 'text-ok' },
    running: { text: 'processing', cls: 'text-accent' },
    failed: { text: 'failed', cls: 'text-err' },
  }

  $: steps = computeSteps(stage, status, completed, stageHistory)
  $: done = steps.filter((s) => s.state === 'done').length
  $: overall =
    status === 'error' || status === 'stopped'
      ? 'failed'
      : status === 'done'
        ? 'done'
        : 'running'
  $: word = WORD[overall]
</script>

<div class="flex items-center gap-2">
  <div class="flex items-center">
    {#each steps as s, i (s.key)}
      <span class="st {LAMP[s.state]}" title="{s.label}: {s.state}"></span>
      {#if i < steps.length - 1}<span class="st-l"></span>{/if}
    {/each}
  </div>
  {#if word}
    <span class="font-mono text-[10px] {word.cls}">
      {word.text}{overall === 'done' ? ` · ${done}/${steps.length}` : ''}
    </span>
  {/if}
</div>
