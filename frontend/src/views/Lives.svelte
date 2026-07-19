<script>
  import { onMount, onDestroy } from 'svelte'
  import { api, isActive } from '../lib/api.js'
  import { href, goto } from '../lib/router.js'
  import PipelineRail from '../components/PipelineRail.svelte'
  import PipelinePanel from '../components/PipelinePanel.svelte'

  export let pid

  let project = null
  let lives = []
  let error = ''
  let showSubmit = false
  let url = ''
  let busy = false
  let timer

  // The pipeline detail (moved here from the Cut room): clicking a row's eye
  // opens the selected live's pipeline section to the right of the table. Its
  // per-step log needs the full /report payload, fetched on demand.
  let pipeLid = null // selected live id, or null when the panel is closed
  let pipeData = null
  let pipeErr = ''

  async function loadProject() {
    try { project = await api.project(pid) } catch (_) { project = null }
  }
  async function loadLives() {
    try {
      lives = await api.lives(pid)
      error = ''
    } catch (e) {
      error = e.message
    }
  }
  async function loadPipe() {
    if (!pipeLid) return
    try { pipeData = await api.report(pid, pipeLid); pipeErr = '' }
    catch (e) { pipeErr = e.message }
  }
  function togglePipe(l) {
    if (pipeLid === l.id) { pipeLid = null; pipeData = null; pipeErr = ''; return }
    pipeLid = l.id
    pipeData = null
    pipeErr = ''
    loadPipe()
  }
  function pipeActive() {
    if (!pipeLid) return false
    const l = lives.find((x) => x.id === pipeLid)
    return !!l && isActive(l.status)
  }
  function pending() {
    return lives.some((l) => isActive(l.status)) || pipeActive()
  }
  onMount(() => {
    loadProject()
    loadLives()
    timer = setInterval(() => {
      if (pending()) { loadLives(); if (pipeActive()) loadPipe() }
    }, 2000)
  })
  onDestroy(() => clearInterval(timer))

  async function submit() {
    if (!url || busy) return
    busy = true
    error = ''
    try {
      const res = await api.submit(pid, url)
      window.dispatchEvent(new Event('primocut:refresh')) // sidebar live_count
      goto(href.cutroom(pid, res.video_id))
    } catch (e) {
      error = e.message
    } finally {
      busy = false
    }
  }
</script>

<div class="flex items-end justify-between gap-3 mb-5 max-w-[1160px]">
  <div><div class="eyebrow">{project?.name || '…'}</div><h1>Lives</h1></div>
  <button class="btn btn-pri" on:click={() => (showSubmit = !showSubmit)}>+ New live</button>
</div>

{#if showSubmit}
  <div class="card mb-4 max-w-[1160px]">
    <div class="flex gap-2.5 items-center">
      <input
        type="text"
        placeholder="https://www.youtube.com/watch?v=..."
        bind:value={url}
        on:keydown={(e) => e.key === 'Enter' && submit()}
      />
      <button class="btn btn-pri shrink-0" on:click={submit} disabled={busy || !url}>
        {busy ? 'Processing…' : 'Process'}
      </button>
    </div>
    <p class="m text-[10.5px] mt-2.5">Lands in {project?.name || 'this niche'}. The project comes from context — you don't pick it twice.</p>
  </div>
{/if}

{#if error}<p class="m text-err mb-4 max-w-[1160px]">{error}</p>{/if}

{#if lives.length === 0}
  <div class="border border-line rounded-card py-10 text-center bg-panel max-w-[1160px]">
    <h2 class="mb-1.5">No lives in this niche yet</h2>
    <p class="text-ink-2">Paste a live URL and it lands straight in {project?.name || 'this niche'}.</p>
    <button class="btn btn-pri mt-4" on:click={() => (showSubmit = true)}>+ New live</button>
  </div>
{:else}
  <!-- Closed, the table keeps the comfortable reading measure; open, the row
       widens to the full canvas so the table shrinks and the pipeline panel
       fits on the same line beside it. -->
  <div class="flex items-start gap-5 {pipeLid ? '' : 'max-w-[1160px]'}">
    <table class="flex-1 min-w-0 border-collapse text-[13px]">
      <thead>
        <tr><th>Live</th><th>Source</th><th class="text-right">Topics</th><th class="text-right">Pipeline</th></tr>
      </thead>
      <tbody>
        {#each lives as l (l.id)}
          <tr
            class="cursor-pointer {pipeLid === l.id ? 'bg-[#15181C]' : 'hover:bg-[#15181C]'}"
            tabindex="0"
            on:click={() => goto(href.cutroom(pid, l.id))}
            on:keydown={(e) => e.key === 'Enter' && goto(href.cutroom(pid, l.id))}
          >
            <td>{l.title || l.id}</td>
            <td class="text-ink-2">{l.channel || '—'}</td>
            <td class="text-right font-mono text-[12px]" class:text-ink-3={!l.topics}>{l.topics || '—'}</td>
            <td>
              <div class="flex items-center justify-end gap-2.5">
                {#if l.status === 'done'}
                  <span class="tag bg-ok-bg text-ok border-ok-line">complete</span>
                {:else}
                  <PipelineRail stage={l.stage} status={l.status} />
                {/if}
                <button
                  type="button"
                  class="shrink-0 p-1 -m-1 rounded cursor-pointer text-ink-3 hover:text-ink"
                  class:text-accent={pipeLid === l.id}
                  title="Show the pipeline"
                  aria-label="Show the pipeline"
                  aria-pressed={pipeLid === l.id}
                  on:click|stopPropagation={() => togglePipe(l)}
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                    <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                    <circle cx="12" cy="12" r="3" />
                  </svg>
                </button>
              </div>
            </td>
          </tr>
        {/each}
      </tbody>
    </table>

    {#if pipeLid}
      <aside class="w-[440px] shrink-0">
        {#if pipeErr}
          <p class="m text-err">{pipeErr}</p>
        {:else if !pipeData}
          <p class="m">Loading…</p>
        {:else}
          <PipelinePanel
            data={pipeData}
            {pid}
            lid={pipeLid}
            on:rerun={() => { loadPipe(); loadLives() }}
            on:error={(e) => (pipeErr = e.detail)}
          />
        {/if}
      </aside>
    {/if}
  </div>
{/if}
