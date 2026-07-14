<script>
  import { createEventDispatcher } from 'svelte'
  import { computeSteps } from '../lib/steps.js'
  import { formatDuration } from '../lib/format.js'

  export let stage = ''
  export let status = 'queued'
  export let progress = 0
  export let completed = {} // STEP.key → boolean (artifact confirmed)
  export let stageHistory = [] // job.stage_history — for per-step durations
  export let active = false // pipeline still running → show the Stop button

  const dispatch = createEventDispatcher()

  const badgeText = {
    done: 'Done', running: 'Processing', failed: 'Failed',
    pending: 'Pending', empty: 'No data', skipped: 'Skipped',
  }

  $: steps = computeSteps(stage, status, completed, stageHistory)
</script>

<article class="pipeline card">
  <div class="card-head">
    <div class="head-left">
      <h3>Pipeline stages</h3>
      <div class="dots">
        {#each steps as s, i (s.key)}
          <span class="dot st-{s.state}" title={s.label}></span>
          {#if i < steps.length - 1}<span class="dot-link"></span>{/if}
        {/each}
      </div>
    </div>
    {#if active}
      <button class="mini danger" on:click={() => dispatch('stop')}>Stop</button>
    {:else}
      <div class="head-actions">
        <button class="mini" on:click={() => dispatch('reprocess')}>Reprocess</button>
        <button class="mini danger" on:click={() => dispatch('remove')}>Delete video</button>
      </div>
    {/if}
  </div>

  {#if status !== 'done'}
    <div class="pbar" title={`${progress}%`}>
      <div class="pfill" style="width:{progress}%"></div>
    </div>
  {/if}

  <div class="pipe-details">
    <ul>
      {#each steps as s (s.key)}
        <li>
          <span>
            {#if s.state === 'running'}<span class="pulse-dot"></span>{/if}
            {s.label}
          </span>
          <span class="li-right">
            {#if s.durationSec != null}
              <span class="stage-dur">{formatDuration(s.durationSec)}</span>
            {/if}
            <span class="badge st-{s.state}">{badgeText[s.state]}</span>
          </span>
        </li>
      {/each}
    </ul>
  </div>
</article>
