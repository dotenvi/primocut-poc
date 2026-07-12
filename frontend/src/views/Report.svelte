<script>
  import { onMount, onDestroy } from 'svelte'
  import { api, isActive } from '../lib/api.js'
  import { hms } from '../lib/format.js'
  import PipelinePanel from '../components/PipelinePanel.svelte'

  export let id

  let data = null
  let error = ''
  let query = ''
  let timer

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

  async function mark(kind, item) {
    const next = item.status === 'marcado' ? 'novo' : 'marcado'
    try {
      if (kind === 'topic') await api.markTopic(id, item.id, next)
      else await api.markMoment(id, item.id, next)
      item.status = next
      data = data // dispara reatividade
    } catch (e) {
      error = e.message
    }
  }

  async function reprocess() {
    try { await api.reenqueue(id); load() } catch (e) { error = e.message }
  }

  async function stop() {
    try { await api.stop(id); load() } catch (e) { error = e.message }
  }

  async function remove() {
    if (!confirm('Deletar TUDO sobre este vídeo? Isso é irreversível — para reprocessar você precisará reenviar a URL.')) return
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
  <p class="muted">Carregando…</p>
{:else}
  <header class="report-head">
    <h1>{data.video.title}</h1>
    <div class="muted">
      {data.video.channel} · {hms(data.video.duration)} ·
      <span class="status status-{data.video.status}">{data.video.status}</span>
    </div>
    <div class="actions">
      <a class="btn" href={api.exportUrl(id)} download>Exportar cortes (JSON)</a>
      <button class="btn ghost" on:click={reprocess}>Reprocessar</button>
      {#if isActive(data.video.status)}
        <button class="btn ghost" on:click={stop}>Interromper</button>
      {:else}
        <button class="btn danger" on:click={remove}>Deletar</button>
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
      <h3>⚠️ Execução degradada</h3>
      <ul>{#each data.warnings as w}<li>{w}</li>{/each}</ul>
    </section>
  {/if}

  <section>
    <h2>Atividade do chat</h2>
    {#if data.chat_signal}
      <div class="spark">
        {#each data.chat_signal.per_minute as b}
          <div
            class="spark-bar"
            style="height:{Math.round((b.msgs / maxMsgs) * 100)}%"
            title={`min ${b.minute}: ${b.msgs} msgs, ${b.laughs} risadas`}
          ></div>
        {/each}
      </div>
      {#if data.chat_signal.peaks.length}
        <div class="peaks">
          Picos:
          {#each data.chat_signal.peaks as p}
            <a href={p.youtube_url} target="_blank" rel="noreferrer">{p.hms}</a>
          {/each}
        </div>
      {:else}
        <p class="muted">Nenhum pico significativo.</p>
      {/if}
    {:else}
      <p class="muted">Sem replay de chat.</p>
    {/if}
  </section>

  <section>
    <h2>Tópicos macro ({data.topics.length})</h2>
    {#each data.topics as t (t.id)}
      <div class="item {t.status === 'marcado' ? 'marked' : ''}">
        <div class="grow">
          <a href={t.youtube_url} target="_blank" rel="noreferrer"><b>{t.title}</b></a>
          {#if t.fire}<span title="coincide com pico do chat">🔥</span>{/if}
          <span class="muted"> · {t.start_hms}–{t.end_hms} · score {t.score.toFixed(2)}</span>
          <div class="muted">{t.summary}</div>
        </div>
        <button class="cut" on:click={() => mark('topic', t)}>
          {t.status === 'marcado' ? '✓ corte' : 'marcar'}
        </button>
      </div>
    {/each}
  </section>

  <section>
    <h2>Momentos de corte ({data.moments.length})</h2>
    {#each data.moments as m (m.id)}
      <div class="item {m.status === 'marcado' ? 'marked' : ''}">
        <div class="grow">
          <a href={m.youtube_url} target="_blank" rel="noreferrer"><b>{m.title}</b></a>
          {#if m.fire}<span title="coincide com pico do chat">🔥</span>{/if}
          <span class="muted"> · {m.start_hms}–{m.end_hms} · score {m.score.toFixed(2)}</span>
          <div class="muted">{m.reason}</div>
        </div>
        <button class="cut" on:click={() => mark('moment', m)}>
          {m.status === 'marcado' ? '✓ corte' : 'marcar'}
        </button>
      </div>
    {/each}
  </section>

  <section>
    <h2>Transcrição</h2>
    <input class="search" placeholder="buscar na transcrição…" bind:value={query} />
    <div class="transcript">
      {#each segs as s (s.index)}
        <p>
          <a class="ts" href={s.youtube_url} target="_blank" rel="noreferrer">{s.start_hms}</a>
          {s.text}
        </p>
      {/each}
    </div>
  </section>
{/if}
