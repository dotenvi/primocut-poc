<script>
  import { computeSteps } from '../lib/steps.js'

  export let stage = ''
  export let status = 'queued'
  export let progress = 0
  export let completed = {} // STEP.key → boolean (artifact confirmed)

  const badgeText = {
    done: 'Done', running: 'Processing', failed: 'Failed',
    pending: 'Pending', skipped: 'Skipped',
  }

  $: steps = computeSteps(stage, status, completed)
</script>

<article class="pipeline card">
  <h3>Pipeline stages</h3>

  {#if status !== 'done'}
    <div class="pbar" title={`${progress}%`}>
      <div class="pfill" style="width:{progress}%"></div>
    </div>
  {/if}

  <div class="dots">
    {#each steps as s, i (s.key)}
      <span class="dot st-{s.state}" title={s.label}></span>
      {#if i < steps.length - 1}<span class="dot-link"></span>{/if}
    {/each}
  </div>

  <details class="pipe-details">
    <summary>Show details</summary>
    <ul>
      {#each steps as s (s.key)}
        <li>
          <span>
            {#if s.state === 'running'}<span class="pulse-dot"></span>{/if}
            {s.label}
          </span>
          <span class="badge st-{s.state}">{badgeText[s.state]}</span>
        </li>
      {/each}
    </ul>
  </details>
</article>
