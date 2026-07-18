<script>
  import { onMount, onDestroy } from 'svelte'
  import { api, isActive } from '../lib/api.js'
  import { href } from '../lib/router.js'
  import { multiplier, compactNumber } from '../lib/format.js'
  import Player from '../components/Player.svelte'
  import PipelineRail from '../components/PipelineRail.svelte'

  export let pid
  export let lid

  let data = null
  let kit = null // project dashboard → the packaging kit
  let project = null
  let error = ''
  let timer
  let playerRef
  let selected = null // selected topic/peak key
  let showLog = false

  function pendingReport() {
    return !data || (data.video.status !== 'done' && data.video.status !== 'error')
  }

  async function loadReport() {
    try { data = await api.report(pid, lid); error = '' } catch (e) { error = e.message }
  }
  async function loadKit() {
    try { kit = await api.projectDashboard(pid) } catch (_) { kit = null }
    try { project = await api.project(pid) } catch (_) { project = null }
  }

  onMount(() => {
    loadReport()
    loadKit()
    timer = setInterval(() => { if (pendingReport()) loadReport() }, 2000)
  })
  onDestroy(() => clearInterval(timer))

  // Seek the player and bring it into view at the top of the page (project rule:
  // every clickable timestamp seeks the player and scrolls to the top).
  function seekTo(seconds, youtubeUrl) {
    const ok = playerRef && playerRef.seek(seconds)
    if (!ok) { window.open(youtubeUrl, '_blank', 'noreferrer'); return }
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  async function rerun() {
    try { await api.reenqueue(pid, lid); await loadReport() } catch (e) { error = e.message }
  }

  $: running = data ? isActive(data.video.status) : false
  $: completed = data
    ? {
        metadata: !!data.video,
        audio: data.transcript.length > 0,
        transcription: data.transcript.length > 0,
        chat: !!data.chat_signal,
        topics: data.topics.length > 0,
      }
    : {}

  // Chat peaks join the topic list as first-class clip candidates, sorted with
  // the topics by their moment in the live.
  $: items = data
    ? [
        ...data.topics.map((t) => ({
          key: `t${t.id}`, kind: 'topic', start: t.start, title: t.title,
          meta: `${t.start_hms}–${t.end_hms} · score ${t.score.toFixed(2)}`,
          youtube_url: t.youtube_url, fire: t.fire,
        })),
        ...((data.chat_signal && data.chat_signal.peaks) || []).map((p, i) => ({
          key: `p${i}`, kind: 'peak', start: p.ts, title: 'Chat peak',
          meta: `${p.hms} · chat spike`, youtube_url: p.youtube_url,
        })),
      ].sort((a, b) => a.start - b.start)
    : []

  function pick(it) {
    selected = it.key
    seekTo(it.start, it.youtube_url)
  }

  // The packaging kit, drawn from the niche's patterns (long-form default).
  $: patterns = (kit && kit.patterns && kit.patterns.long ? kit.patterns.long : [])
    .filter((p) => p.verdict === 'positive')
    .slice(0, 5)
  $: refOutliers = (kit && kit.outliers && kit.outliers.long ? kit.outliers.long : []).slice(0, 3)
  $: asks = kit
    ? Object.values(kit.audience_requests.by_type || {}).flat().slice(0, 6).map((r) => r.value)
    : []

  function copyBrief() {
    const lines = ['PACKAGING BRIEF — ' + (project?.name || 'niche')]
    if (patterns.length) {
      lines.push('', 'Attributes that lift in this niche:')
      for (const p of patterns) lines.push(`- ${p.attribute} (${multiplier(p.lift)}, n=${p.n_outlier})`)
    }
    if (refOutliers.length) {
      lines.push('', 'Reference outliers:')
      for (const o of refOutliers) lines.push(`- ${multiplier(o.median_multiple)} ${o.title || o.video_id}`)
    }
    if (asks.length) lines.push('', 'Audience asked for: ' + asks.join(', '))
    navigator.clipboard?.writeText(lines.join('\n'))
  }

  const heat = (lift) =>
    lift >= 1.05 ? 'bg-heat-bg99 text-heat-99' : 'bg-card2 text-ink-3'
</script>

<a href={href.lives(pid)} class="eyebrow cursor-pointer hover:text-ink-2 mb-1 block no-underline">‹ {project?.name || 'Lives'} / Lives</a>
<div class="eyebrow text-accent mb-1">Cut room</div>
<h1 class="mb-5">{data?.video.title || lid}</h1>

{#if error}<p class="m text-err mb-4">{error}</p>{/if}

{#if !data}
  <p class="m">Loading…</p>
{:else}
  <div class="grid grid-cols-[1fr_352px] gap-5 items-start max-[960px]:grid-cols-1">
    <div>
      <div class="mb-3">
        <Player bind:this={playerRef} videoId={data.video.id} />
      </div>

      <div class="card mb-5 py-2.5">
        <div class="flex items-center justify-between gap-3">
          <div class="flex items-center gap-3">
            <span class="lbl">Pipeline</span>
            <PipelineRail
              stage={data.job ? data.job.stage : ''}
              status={data.video.status}
              stageHistory={data.job ? data.job.stage_history : []}
              {completed}
            />
          </div>
          <div class="flex gap-1.5">
            <button class="btn btn-xs" on:click={() => (showLog = !showLog)}>Show log</button>
            <button class="btn btn-xs" on:click={rerun}>Rerun stages</button>
          </div>
        </div>
        {#if showLog}
          <div class="mt-2.5 border-t border-line pt-2.5">
            {#if data.warnings && data.warnings.length}
              {#each data.warnings as w}<div class="m text-err">⚠ {w}</div>{/each}
            {/if}
            {#each (data.job && data.job.stage_history) || [] as h}
              <div class="m">{h.stage} · {h.at}</div>
            {:else}
              <div class="m">No stage history yet.</div>
            {/each}
          </div>
        {/if}
      </div>

      <div class="lbl mb-2">Topics · {data.topics.length}</div>
      {#if items.length === 0}
        <p class="m">{running ? 'Processing — topics appear as the pipeline finishes.' : 'No topics yet. Rerun with topics to generate them.'}</p>
      {/if}
      {#each items as it (it.key)}
        <div
          class="topic"
          class:on={selected === it.key}
          role="button"
          tabindex="0"
          on:click={() => pick(it)}
          on:keydown={(e) => e.key === 'Enter' && pick(it)}
        >
          <div>{it.kind === 'peak' ? '🔥 ' : ''}{it.title}{it.fire ? ' 🔥' : ''}</div>
          <div class="m mt-0.5">{it.meta}</div>
        </div>
      {/each}
    </div>

    <div class="sticky top-5">
      <div class="bg-card border border-line rounded-card overflow-hidden">
        <div class="px-4 py-3 border-b border-line flex items-center gap-2">
          <span class="lbl text-accent">Packaging kit</span>
          <span class="tag bg-card2 text-ink-2 border-line-strong ml-auto">{project?.name || 'niche'}</span>
        </div>
        <div class="p-4">
          {#if !kit}
            <p class="m">Loading the niche patterns…</p>
          {:else}
            <p class="m mb-3 leading-relaxed">What separates outliers from the rest across this niche's {kit.channels.length} competitors.</p>
            {#if patterns.length === 0}
              <p class="m">No attribute separates outliers yet — enrich the niche's outliers in Niche › Patterns.</p>
            {/if}
            {#each patterns as p (p.source + p.attribute)}
              <div class="pat">
                <span class="flex-1">{p.attribute}</span>
                <span class="font-mono text-[10px] text-ink-3">n={p.n_outlier}</span>
                <span class="font-mono text-[10.5px] px-1.5 py-px rounded {heat(p.lift)}">{multiplier(p.lift)}</span>
              </div>
            {/each}

            {#if refOutliers.length}
              <div class="lbl mt-5 mb-1">Reference outliers</div>
              {#each refOutliers as o (o.video_id)}
                <a
                  href={href.video(pid, o.channel_id, o.video_id)}
                  class="flex gap-2.5 py-1.5 border-t border-line text-[12px] text-ink-2 leading-snug no-underline hover:text-ink"
                >
                  <span class="font-mono text-[11px] text-heat-99 shrink-0">{multiplier(o.median_multiple)}</span>
                  <span>{o.title || o.video_id}</span>
                </a>
              {/each}
            {/if}

            {#if asks.length}
              <div class="lbl mt-5 mb-1">The audience asked for</div>
              <p class="text-ink-2 text-[12.5px]">{asks.join(', ')}</p>
            {/if}

            <button class="btn w-full mt-4" on:click={copyBrief}>Copy as brief</button>
          {/if}
        </div>
      </div>
    </div>
  </div>
{/if}
