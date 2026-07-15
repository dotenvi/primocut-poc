<script>
  import { onMount, onDestroy } from 'svelte'
  import { api, backfillActive } from '../lib/api.js'

  export let pid

  let channels = null // null while loading
  let error = ''
  let notice = ''
  let url = ''
  let busy = false
  let timer

  async function load() {
    try {
      channels = await api.channels(pid)
      error = ''
    } catch (e) {
      error = e.message
    }
  }

  // Poll only while some backfill is still running (Report.svelte pattern).
  function pending() {
    return !channels || channels.some((c) => backfillActive(c.backfill.state))
  }

  onMount(() => {
    load()
    timer = setInterval(() => { if (pending()) load() }, 2000)
  })
  onDestroy(() => clearInterval(timer))

  async function add() {
    if (!url || busy) return
    busy = true
    error = ''
    notice = ''
    try {
      const res = await api.addChannel(pid, url)
      notice = res.existing
        ? `Linked “${res.title}” — already tracked, no new collection.`
        : `Added “${res.title}” — backfill queued.`
      url = ''
      await load()
    } catch (e) {
      error = e.message
    } finally {
      busy = false
    }
  }

  async function detach(c) {
    if (!confirm(`Detach “${c.title}” from this project? All collected data is kept.`)) return
    error = ''
    notice = ''
    try {
      await api.detachChannel(pid, c.channel_id)
      await load()
    } catch (e) {
      error = e.message
    }
  }

  const pctDone = (b) => (b.total ? Math.round((b.done / b.total) * 100) : 0)
</script>

<article class="card">
  <h3>Add a competitor channel</h3>
  <p class="muted">Paste a channel URL, an @handle or a channel ID. A channel new to the system
    backfills its recent uploads in the background; a known one is only linked.</p>
  <div class="row-input">
    <input
      bind:value={url}
      placeholder="https://www.youtube.com/@handle"
      on:keydown={(e) => e.key === 'Enter' && add()}
    />
    <button on:click={add} disabled={busy || !url}>{busy ? 'Adding…' : 'Add'}</button>
  </div>
  {#if error}<p class="error">{error}</p>{/if}
  {#if notice}<p class="muted">{notice}</p>{/if}
</article>

<section>
  <h2>Channels</h2>
  {#if channels === null}
    <p class="muted">Loading…</p>
  {:else if channels.length === 0}
    <p class="muted">No channels yet — add a competitor above.</p>
  {:else}
    <ul class="list">
      {#each channels as c (c.channel_id)}
        <li class="vrow">
          <div class="vrow-main">
            <div class="grow">
              <a href={`#/projects/${pid}/channels/${c.channel_id}`}>{c.title || c.channel_id}</a>
              <div class="muted">{c.handle}</div>
            </div>
            {#if c.backfill.state === 'done'}
              <span class="status status-done">{c.backfill.total} videos</span>
            {:else if c.backfill.state === 'error'}
              <span class="status status-error">backfill failed</span>
            {:else if backfillActive(c.backfill.state)}
              <span class="status status-queued">{c.backfill.state}</span>
            {:else}
              <span class="status">{c.backfill.state}</span>
            {/if}
            <button class="mini danger" on:click={() => detach(c)}>Detach</button>
          </div>

          {#if backfillActive(c.backfill.state)}
            <div class="progress-line">
              <div class="progress-head">
                <span class="spinner"></span>
                <b class="progress-label">Collecting uploads…</b>
                <span class="progress-count">{c.backfill.done}/{c.backfill.total || '?'}</span>
              </div>
              <div class="progress-bar">
                <div class="progress-fill" style="width:{pctDone(c.backfill)}%"></div>
              </div>
            </div>
          {:else if c.backfill.state === 'error' && c.backfill.error}
            <p class="error">{c.backfill.error}</p>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</section>
