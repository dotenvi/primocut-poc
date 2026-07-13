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

  async function generateTopics() {
    try { await api.generateTopics(id); await load() } catch (e) { error = e.message }
  }

  async function stop() {
    try { await api.stop(id); load() } catch (e) { error = e.message }
  }

  async function remove() {
    if (!confirm('Delete EVERYTHING about this video? This is irreversible — to reprocess you will need to resubmit the URL.')) return
    try { await api.remove(id); location.hash = '#/videos' } catch (e) { error = e.message }
  }

  // Per-step evidence (STEP.key → produced its artifact?). A step is only shown
  // as "done" when confirmed here — never just because the pipeline finished.
  $: completed = data
    ? {
        metadata: !!data.video, // report loaded ⇒ metadata ran
        audio: data.transcript.length > 0, // audio file is ephemeral; transcript ⇒ it ran
        transcription: data.transcript.length > 0,
        chat: !!data.chat_signal,
        topics: data.topics.length > 0,
      }
    : {}

  // The pipeline is "running" while the video sits in an active status (queued →
  // analyzing). Empty/non-interactive sections are hidden until it finishes.
  $: running = data ? isActive(data.video.status) : false

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
    {#if !running}
      <div class="actions">
        <a class="btn" href={api.exportUrl(id)} download>Export cuts (JSON)</a>
        <button class="btn ghost" on:click={reprocess}>Reprocess</button>
        <button class="btn danger" on:click={remove}>Delete</button>
      </div>
    {/if}
  </header>

  {#if pending() || data.job}
    <PipelinePanel
      stage={data.job ? data.job.stage : ''}
      status={data.video.status}
      progress={data.job ? data.job.progress : 0}
      active={running}
      {completed}
      on:stop={stop}
    />
  {/if}

  {#if data.warnings && data.warnings.length}
    <section class="degraded">
      <h3>⚠️ Degraded run</h3>
      <ul>{#each data.warnings as w}<li>{w}</li>{/each}</ul>
    </section>
  {/if}

  {#if !running && data.chat_signal}
    <section>
      <h2>Chat activity</h2>
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
    </section>
  {/if}

  {#if !running}
    <article class="card topics-card">
      <div class="card-head">
        <h3>Topics ({data.topics.length})</h3>
        <div class="card-actions">
          <button class="mini ai" on:click={generateTopics}>
            ✨ {data.topics.length ? 'Regenerate' : 'Generate'} topics with AI
          </button>
          {#if data.topics.length}
            <button class="mini" on:click={expandAll}>Expand all</button>
            <button class="mini" on:click={collapseAll}>Collapse all</button>
          {/if}
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
        <p class="muted">No topics yet — click “Generate topics with AI” to create them.</p>
      {/if}
    </article>
  {/if}

  {#if !running && data.transcript.length}
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
{/if}
