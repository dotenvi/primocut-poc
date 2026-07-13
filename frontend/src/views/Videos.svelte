<script>
  import { onMount, onDestroy } from 'svelte'
  import { api, isActive } from '../lib/api.js'
  import { currentStep } from '../lib/steps.js'

  let videos = []
  let error = ''
  let timer

  async function load() {
    try {
      videos = await api.list()
      error = ''
    } catch (e) {
      error = e.message
    }
  }

  async function stop(id) {
    try { await api.stop(id); load() } catch (e) { error = e.message }
  }

  onMount(() => {
    load()
    timer = setInterval(load, 2000) // live polling
  })
  onDestroy(() => clearInterval(timer))
</script>

<section>
  <h1>Videos</h1>
  {#if error}<p class="error">{error}</p>{/if}
  {#if videos.length === 0}
    <p class="muted">No videos yet. <a href="#/">Submit one</a>.</p>
  {/if}
  <ul class="list">
    {#each videos as v (v.id)}
      <li class="vrow">
        <div class="vrow-main">
          <div class="grow">
            <a href={`#/report/${v.id}`}>{v.title || v.id}</a>
            <div class="muted">{v.channel}</div>
          </div>
          <span class="status status-{v.status}">{v.status}</span>
          {#if isActive(v.status)}
            <button class="mini" on:click|preventDefault={() => stop(v.id)}>Stop</button>
          {/if}
        </div>
        {#if isActive(v.status)}
          {@const step = currentStep(v.stage)}
          <div class="progress-line">
            <div class="progress-head">
              <span class="spinner"></span>
              <b class="progress-label">{step.label}</b>
              <span class="progress-count">{step.number}/{step.total}</span>
            </div>
            <div class="progress-bar"><div class="progress-fill" style="width:{v.progress}%"></div></div>
          </div>
        {/if}
      </li>
    {/each}
  </ul>
</section>
