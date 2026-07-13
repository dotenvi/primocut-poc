<script>
  import { onMount, onDestroy } from 'svelte'
  import { api, isActive } from '../lib/api.js'
  import PipelinePanel from '../components/PipelinePanel.svelte'
  import Player from '../components/Player.svelte'

  export let id

  let data = null
  let error = ''
  let query = ''
  let timer
  let playerRef // Player component instance (bind:this)
  let expanded = {} // topic id -> expanded?

  function pending() {
    return !data || (data.video.status !== 'done' && data.video.status !== 'error')
  }

  async function load() {
    try {
      data = await api.report(id)
      error = ''
    } catch (e) {
      error = e.message
    }
  }

  onMount(() => {
    load()
    timer = setInterval(() => { if (pending()) load() }, 2000)
  })
  onDestroy(() => clearInterval(timer))

  function toggle(tid) {
    expanded = { ...expanded, [tid]: !expanded[tid] }
  }
  function expandAll() {
    const e = {}
    for (const t of data.topics) e[t.id] = true
    expanded = e
  }
  function collapseAll() {
    expanded = {}
  }

  // Seek the embedded player to `seconds`, play, and bring the player into view
  // at the top of the page. If the player is unavailable (embed disabled / failed
  // to load), fall back to opening YouTube at that time instead.
  function seekTo(seconds, youtubeUrl) {
    const ok = playerRef && playerRef.seek(seconds)
    if (!ok) {
      window.open(youtubeUrl, '_blank', 'noreferrer')
      return
    }
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }
  const watch = (t) => seekTo(t.start, t.youtube_url)

  async function reprocess() {
    try { await api.reenqueue(id); load() } catch (e) { error = e.message }
  }

  async function stop() {
    try { await api.stop(id); load() } catch (e) { error = e.message }
  }

  async function remove() {
    if (!confirm('Delete EVERYTHING about this video? This is irreversible — to reprocess you will need to resubmit the URL.')) return
    try { await api.remove(id); location.hash = '#/videos' } catch (e) { error = e.message }
  }

  $: segs = data
    ? data.transcript.filter((s) => !query || s.text.toLowerCase().includes(query.toLowerCase()))
    : []
  $: maxMsgs =
    data && data.chat_signal && data.chat_signal.per_minute.length
      ? Math.max(1, ...data.chat_signal.per_minute.map((b) => b.msgs))
      : 1
</script>

{#if error}<p class="error">{error}</p>{/if}

{#if !data}
  <p class="muted">Loading…</p>
{:else}
  <header class="report-head">
    <Player bind:this={playerRef} videoId={data.video.id} />
    <div class="actions">
      <a class="btn" href={api.exportUrl(id)} download>Export cuts (JSON)</a>
      <button class="btn ghost" on:click={reprocess}>Reprocess</button>
      {#if isActive(data.video.status)}
        <button class="btn ghost" on:click={stop}>Stop</button>
      {:else}
        <button class="btn danger" on:click={remove}>Delete</button>
      {/if}
    </div>
  </header>

  {#if pending() || data.job}
    <PipelinePanel
      stage={data.job ? data.job.stage : ''}
      status={data.video.status}
      progress={data.job ? data.job.progress : 0}
    />
  {/if}

  {#if data.warnings && data.warnings.length}
    <section class="degraded">
      <h3>⚠️ Degraded run</h3>
      <ul>{#each data.warnings as w}<li>{w}</li>{/each}</ul>
    </section>
  {/if}

  <section>
    <h2>Chat activity</h2>
    {#if data.chat_signal}
      <div class="spark">
        {#each data.chat_signal.per_minute as b}
          <div
            class="spark-bar"
            style="height:{Math.round((b.msgs / maxMsgs) * 100)}%"
            title={`min ${b.minute}: ${b.msgs} msgs, ${b.laughs} laughs`}
          ></div>
        {/each}
      </div>
      {#if data.chat_signal.peaks.length}
        <div class="peaks">
          Peaks:
          {#each data.chat_signal.peaks as p}
            <button class="peak-link" on:click={() => seekTo(p.ts, p.youtube_url)}>{p.hms}</button>
          {/each}
        </div>
      {:else}
        <p class="muted">No significant peak.</p>
      {/if}
    {:else}
      <p class="muted">No chat replay.</p>
    {/if}
  </section>

  <article class="card topics-card">
    <div class="card-head">
      <h3>Topics ({data.topics.length})</h3>
      <div class="card-actions">
        <button class="mini" on:click={expandAll}>Expand all</button>
        <button class="mini" on:click={collapseAll}>Collapse all</button>
      </div>
    </div>
    {#each data.topics as t (t.id)}
      <div class="topic">
        <div class="topic-head">
          <button class="disclosure" on:click={() => toggle(t.id)} aria-expanded={!!expanded[t.id]}>
            <span class="caret">{expanded[t.id] ? '▾' : '▸'}</span>
            <b>{t.title}</b>
          </button>
          {#if t.fire}<span class="fire" title="coincides with a chat peak">🔥</span>{/if}
          <button class="mini watch" on:click={() => watch(t)}>▶ Watch</button>
        </div>
        <div class="topic-meta muted">{t.start_hms}–{t.end_hms} · score {t.score.toFixed(2)}</div>
        {#if expanded[t.id]}
          <div class="topic-body muted">{t.summary}</div>
        {/if}
      </div>
    {/each}
    {#if data.topics.length === 0}
      <p class="muted">No topic identified.</p>
    {/if}
  </article>

  <section>
    <h2>Transcript</h2>
    <input class="search" placeholder="search the transcript…" bind:value={query} />
    <div class="transcript">
      {#each segs as s (s.index)}
        <p>
          <button class="ts" on:click={() => seekTo(s.start, s.youtube_url)}>{s.start_hms}</button>
          {s.text}
        </p>
      {/each}
    </div>
  </section>
{/if}
