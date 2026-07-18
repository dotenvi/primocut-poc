<script>
  import { onMount, onDestroy } from 'svelte'
  import { api, isActive } from '../lib/api.js'
  import { href, goto } from '../lib/router.js'
  import PipelineRail from '../components/PipelineRail.svelte'

  export let pid

  let project = null
  let lives = []
  let error = ''
  let showSubmit = false
  let url = ''
  let busy = false
  let timer

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
  function pending() {
    return lives.some((l) => isActive(l.status))
  }
  onMount(() => {
    loadProject()
    loadLives()
    timer = setInterval(() => { if (pending()) loadLives() }, 2000)
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

<div class="flex items-end justify-between gap-3 mb-5">
  <div><div class="eyebrow">{project?.name || '…'}</div><h1>Lives</h1></div>
  <button class="btn btn-pri" on:click={() => (showSubmit = !showSubmit)}>+ New live</button>
</div>

{#if showSubmit}
  <div class="card mb-4">
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

{#if error}<p class="m text-err mb-4">{error}</p>{/if}

{#if lives.length === 0}
  <div class="border border-line rounded-card py-10 text-center bg-panel">
    <h2 class="mb-1.5">No lives in this niche yet</h2>
    <p class="text-ink-2">Paste a live URL and it lands straight in {project?.name || 'this niche'}.</p>
    <button class="btn btn-pri mt-4" on:click={() => (showSubmit = true)}>+ New live</button>
  </div>
{:else}
  <table class="w-full border-collapse text-[13px]">
    <thead>
      <tr><th>Live</th><th>Source</th><th class="text-right">Topics</th><th class="w-36">Pipeline</th></tr>
    </thead>
    <tbody>
      {#each lives as l (l.id)}
        <tr
          class="hover:bg-[#15181C] cursor-pointer"
          tabindex="0"
          on:click={() => goto(href.cutroom(pid, l.id))}
          on:keydown={(e) => e.key === 'Enter' && goto(href.cutroom(pid, l.id))}
        >
          <td>{l.title || l.id}</td>
          <td class="text-ink-2">{l.channel || '—'}</td>
          <td class="text-right font-mono text-[12px]" class:text-ink-3={!l.topics}>{l.topics || '—'}</td>
          <td>
            {#if l.status === 'done'}
              <span class="tag bg-ok-bg text-ok border-ok-line">complete</span>
            {:else if l.status === 'error' || l.status === 'stopped'}
              <PipelineRail stage={l.stage} status={l.status} />
            {:else}
              <PipelineRail stage={l.stage} status={l.status} />
            {/if}
          </td>
        </tr>
      {/each}
    </tbody>
  </table>
{/if}
