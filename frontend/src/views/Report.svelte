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
  let activeTab = '' // selected report tab; fixed up reactively to a valid one

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

  // Report body sections shown as tabs once the pipeline finishes. Only tabs
  // with something to show are listed; Topics is always available (it can be
  // generated on demand).
  $: tabs =
    data && !running
      ? [
          data.chat_signal && { id: 'chat', label: 'Chat activity' },
          { id: 'topics', label: `Topics (${data.topics.length})` },
          data.transcript.length && { id: 'transcript', label: 'Transcript' },
        ].filter(Boolean)
      : []
  // Keep the selection valid as tabs appear/disappear (default to the first).
  $: if (tabs.length && !tabs.some((t) => t.id === activeTab)) activeTab = tabs[0].id
</script>

{#if error}<p class="error">{error}</p>{/if}

{#if !data}
  <p class="muted">Loading…</p>
{:else}
  <div class="report-layout" class:split={!running && tabs.length}>
  <div class="report-main">
  <header class="report-head">
    <Player bind:this={playerRef} videoId={data.video.id} />
  </header>

  {#if pending() || data.job}
    <PipelinePanel
      stage={data.job ? data.job.stage : ''}
      status={data.video.status}
      progress={data.job ? data.job.progress : 0}
      stageHistory={data.job ? data.job.stage_history : []}
      active={running}
      {completed}
      on:stop={stop}
      on:reprocess={reprocess}
      on:remove={remove}
    />
  {/if}

  {#if data.warnings && data.warnings.length}
    <section class="degraded">
      <h3>⚠️ Degraded run</h3>
      <ul>{#each data.warnings as w}<li>{w}</li>{/each}</ul>
    </section>
  {/if}

  </div><!-- .report-main -->

  {#if !running && tabs.length}
    <aside class="report-side">
    <article class="card tabs-card">
      <div class="tab-bar" role="tablist">
        {#each tabs as t (t.id)}
          <button
            class="tab"
            class:active={activeTab === t.id}
            role="tab"
            aria-selected={activeTab === t.id}
            on:click={() => (activeTab = t.id)}
          >{t.label}</button>
        {/each}
        {#if activeTab === 'topics'}
          <button class="mini ai tab-bar-action" on:click={generateTopics}>
            ✨ {data.topics.length ? 'Regenerate' : 'Generate'} topics with AI
          </button>
        {/if}
      </div>

      {#if activeTab === 'chat' && data.chat_signal}
        <div class="tab-panel" role="tabpanel">
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
        </div>
      {:else if activeTab === 'topics'}
        <div class="tab-panel" role="tabpanel">
          {#if !data.topics.length}
            <p class="muted">No topics yet — click “Generate topics with AI” to create them.</p>
          {/if}
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
        </div>
      {:else if activeTab === 'transcript' && data.transcript.length}
        <div class="tab-panel" role="tabpanel">
          <input class="search" placeholder="search the transcript…" bind:value={query} />
          <div class="transcript">
            {#each segs as s (s.index)}
              <p>
                <button class="ts" on:click={() => seekTo(s.start, s.youtube_url)}>{s.start_hms}</button>
                {s.text}
              </p>
            {/each}
          </div>
        </div>
      {/if}
    </article>
    </aside>
  {/if}
  </div><!-- .report-layout -->

{/if}
