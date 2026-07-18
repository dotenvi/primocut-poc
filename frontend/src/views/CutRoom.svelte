<script>
  import { onMount, onDestroy } from 'svelte'
  import { api, isActive } from '../lib/api.js'
  import { href } from '../lib/router.js'
  import { multiplier, compactNumber, formatDuration } from '../lib/format.js'
  import { computeSteps } from '../lib/steps.js'
  import Player from '../components/Player.svelte'
  import PipelineRail from '../components/PipelineRail.svelte'

  // Per-state status chip for the log: a round icon (right-aligned) plus its
  // word. Kept in lock-step with computeSteps' state vocabulary so the log and
  // the rail never disagree. Order of the log itself is the canonical pipeline
  // execution order (STEPS), not the raw transition history.
  const STATUS = {
    done:    { word: 'complete',   wcls: 'text-ok',    ring: 'bg-ok text-white',                           glyph: '✓' },
    failed:  { word: 'failed',     wcls: 'text-err',   ring: 'bg-err text-white',                          glyph: '✕' },
    running: { word: 'processing', wcls: 'text-accent',ring: 'bg-accent text-accent-on animate-pulse',     glyph: '' },
    pending: { word: 'queued',     wcls: 'text-ink-3', ring: 'border border-line',                         glyph: '' },
    empty:   { word: 'ignored',    wcls: 'text-ink-3', ring: 'bg-line-strong',                             glyph: '' },
    skipped: { word: 'skipped',    wcls: 'text-ink-3', ring: 'border border-dashed border-line-strong text-ink-3', glyph: '/' },
  }

  export let pid
  export let lid

  // Minimal in-card scrollbar as Tailwind arbitrary-property utilities (thin,
  // no hover growth, no OS arrow buttons). #333B44 is colors.line.strong; a
  // reserved gutter keeps content from reflowing when the bar appears.
  const SCROLL =
    '[scrollbar-gutter:stable] [scrollbar-width:thin] [scrollbar-color:#333B44_transparent]'

  let data = null
  let kit = null // project dashboard → the packaging kit
  let project = null
  let error = ''
  let timer
  let playerRef
  let selected = null // selected topic/peak key

  // Match the Topics card height to the player+pipeline column (so it bottom-
  // aligns with the pipeline card, including when its log is open). Pure CSS
  // can't do this: the always-long topics list would inflate the grid row and
  // drive the height itself. Instead we measure the left column and, only in the
  // 3-column layout, apply its height to the card; the list scrolls within.
  const THREE_COL = '(min-width: 1561px)'
  let matchHeight = null
  const mq = typeof window !== 'undefined' ? window.matchMedia(THREE_COL) : null

  // Svelte action: observe the left column and mirror its height into
  // `matchHeight` while the 3-column layout is active (null otherwise, so the
  // card falls back to its CSS viewport cap in the stacked layout).
  function matchSource(node) {
    const update = () => { matchHeight = mq && mq.matches ? node.offsetHeight : null }
    const ro = new ResizeObserver(update)
    ro.observe(node)
    mq?.addEventListener('change', update)
    update()
    return { destroy() { ro.disconnect(); mq?.removeEventListener('change', update) } }
  }

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
  // The log, in canonical pipeline execution order (never raw history order),
  // with each step's settled state + duration from the shared stage truth.
  $: logSteps = data
    ? computeSteps(
        data.job ? data.job.stage : '',
        data.video.status,
        completed,
        data.job ? data.job.stage_history : []
      )
    : []
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
          time: `${t.start_hms}–${t.end_hms}`, meta: `score ${t.score.toFixed(2)}`,
          summary: t.summary, youtube_url: t.youtube_url, fire: t.fire,
        })),
        ...((data.chat_signal && data.chat_signal.peaks) || []).map((p, i) => ({
          key: `p${i}`, kind: 'peak', start: p.ts, title: 'Chat peak',
          time: p.hms, meta: 'chat spike', youtube_url: p.youtube_url,
        })),
      ].sort((a, b) => a.start - b.start)
    : []

  // Two separate actions per item: the timestamp plays (seek, per the project
  // rule), and — independently — each item's description expands/collapses.
  let expanded = {} // key → bool
  const expandable = (it) => it.kind === 'topic' && !!it.summary
  function play(it) {
    selected = it.key
    seekTo(it.start, it.youtube_url)
  }
  function toggle(it) {
    if (expandable(it)) expanded = { ...expanded, [it.key]: !expanded[it.key] }
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
  <!-- Player is the flexible column (fills the leftover); the two side cards have
       fixed widths so the player doesn't sprawl on big screens. Both cards mirror
       this left column's height (via `matchSource` → `matchHeight`), so all three
       columns bottom-align. -->
  <div class="grid grid-cols-[minmax(0,1fr)_460px_400px] gap-5 items-start max-[1560px]:grid-cols-2 max-[960px]:grid-cols-1">
    <!-- Below 1560px the player takes the full width on top and Topics /
         Packaging kit sit side by side beneath it; on wider screens all three
         are columns in one row. -->
    <div class="max-[1560px]:col-span-2 max-[960px]:col-span-1" use:matchSource>
      <div class="mb-3">
        <Player bind:this={playerRef} videoId={data.video.id} />
      </div>

      <div class="card py-2.5">
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
            <button class="btn btn-xs" on:click={rerun}>Rerun stages</button>
          </div>
        </div>
        <div class="mt-2.5 border-t border-line pt-2.5">
          {#if data.warnings && data.warnings.length}
            {#each data.warnings as w}<div class="m text-err mb-1.5">⚠ {w}</div>{/each}
          {/if}
          {#each logSteps as s (s.key)}
            {@const st = STATUS[s.state] || STATUS.pending}
            <div class="flex items-center justify-between py-1.5 border-t border-line first:border-t-0">
              <span class="m text-ink-2">{s.label}</span>
              <div class="flex items-center gap-2.5">
                <span class="m text-ink-3 tabular-nums">{formatDuration(s.durationSec)}</span>
                <span class="font-mono text-[10px] {st.wcls} w-[70px] text-right">{st.word}</span>
                <span
                  class="inline-flex items-center justify-center w-4 h-4 rounded-full text-[9px] leading-none font-bold shrink-0 {st.ring}"
                  title={st.word}
                >{st.glyph}</span>
              </div>
            </div>
          {/each}
        </div>
      </div>
    </div>

    <!-- Beside the player: the clip candidates (topics + chat peaks). Its height
         is pinned to the left column (`matchHeight`) so it bottom-aligns with the
         pipeline card; the list scrolls inside with the custom scrollbar. When
         stacked (matchHeight null) it falls back to the viewport cap below. -->
    <div
      class="bg-card border border-line rounded-card overflow-hidden flex flex-col max-h-[calc(100vh-2.5rem)]"
      style:height={matchHeight ? `${matchHeight}px` : null}
    >
      <div class="px-4 py-3 border-b border-line flex items-center gap-2 shrink-0">
        <span class="lbl text-accent">Topics</span>
        <span class="tag bg-card2 text-ink-2 border-line-strong ml-auto">{data.topics.length}</span>
      </div>
      <div class="p-2 flex-1 overflow-y-auto min-h-0 {SCROLL}">
        {#if items.length === 0}
          <p class="m px-1.5 py-2">{running ? 'Processing — topics appear as the pipeline finishes.' : 'No topics yet. Rerun with topics to generate them.'}</p>
        {/if}
        {#each items as it (it.key)}
          {@const isOpen = !!expanded[it.key]}
          <div class="topic" class:on={selected === it.key}>
            <!-- Title expands/collapses the description (topics with a summary);
                 for the rest it plays. The timestamp below always plays. -->
            <button
              type="button"
              class="w-full text-left cursor-pointer"
              aria-expanded={expandable(it) ? isOpen : undefined}
              on:click={() => (expandable(it) ? toggle(it) : play(it))}
            >{it.kind === 'peak' ? '🔥 ' : ''}{it.title}{it.fire ? ' 🔥' : ''}</button>
            <div class="m mt-0.5">
              <button type="button" class="text-accent hover:text-accent-hi cursor-pointer" on:click={() => play(it)}>{it.time}</button>
              <span class="text-ink-3"> · {it.meta}</span>
            </div>
            {#if isOpen && it.summary}
              <p class="text-ink-2 text-[12.5px] leading-relaxed mt-1.5">{it.summary}</p>
            {/if}
          </div>
        {/each}
      </div>
    </div>

    <!-- Height aligned with the Topics card / player column (`matchHeight`); its
         content scrolls inside with the custom scrollbar and the action stays
         pinned as a footer. -->
    <div
      class="bg-card border border-line rounded-card overflow-hidden flex flex-col max-h-[calc(100vh-2.5rem)]"
      style:height={matchHeight ? `${matchHeight}px` : null}
    >
      <div class="px-4 py-3 border-b border-line flex items-center gap-2 shrink-0">
        <span class="lbl text-accent">Packaging kit</span>
        <span class="tag bg-card2 text-ink-2 border-line-strong ml-auto">{project?.name || 'niche'}</span>
      </div>
      <div class="p-4 flex-1 overflow-y-auto min-h-0 {SCROLL}">
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
        {/if}
      </div>
      {#if kit}
        <div class="p-4 pt-3 border-t border-line shrink-0">
          <button class="btn w-full" on:click={copyBrief}>Copy as brief</button>
        </div>
      {/if}
    </div>
  </div>
{/if}
