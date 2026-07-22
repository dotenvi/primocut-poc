<script>
  import { onMount, onDestroy } from 'svelte'
  import { api, isActive, clipActive, exportActive, cutPackagingActive, packagingActive } from '../lib/api.js'
  import { href } from '../lib/router.js'
  import { hms, multiplier } from '../lib/format.js'
  import Player from '../components/Player.svelte'

  export let pid
  export let lid

  // Minimal in-card scrollbar (thin, no hover growth, reserved gutter) — the
  // same treatment used across the app's scrolling cards.
  const SCROLL =
    '[scrollbar-gutter:stable] [scrollbar-width:thin] [scrollbar-color:#333B44_transparent]'

  // ------------------------- state -------------------------
  let report = null // transcript + video meta + pipeline status
  let bench = null // the active cut's items + every topic's derived state
  let cuts = [] // the live's cuts (for the selector)
  let activeCutId = null
  let kit = null // project dashboard → the packaging kit reference (read-only)
  let pctx = null // the niche packaging context (three lenses) for generation
  let project = null

  // AI packaging generation controls (opt-in — each LLM pass costs money).
  let titlesK = 5 // how many title candidates to propose per clip
  let framesCount = 3 // how many candidate frames to extract per clip
  let pkgBusy = '' // which packaging action is being dispatched (button spinner)
  let matchingAudience = false
  let error = ''
  let timer
  let playerRef

  let mode = 'source' // 'source' | 'preview'
  let selected = null // selected topic id (drives seek + transcript highlight)
  let query = '' // transcript search filter
  let showKit = false // packaging kit is collapsed by default

  // Manual-range composer (col 3).
  let manualOpen = false
  let manualStart = ''
  let manualEnd = ''

  // Virtual preview driver.
  let previewIndex = 0
  let previewPlaying = false
  let gapLabel = null // overlay text while the player buffers between items
  let driver // setInterval id
  let gapTimer

  const ORDER = ['open', 'in', 'maybe']
  const ICON = { open: '○', in: '✓', maybe: '◐', discarded: '✕' }

  let rendering = false // a render request is in flight / clips are still working
  let exporting = false // an export request is in flight
  let upscale = false // --upscale-1440p toggle for the export
  const CLIP_LABEL = {
    queued: 'queued', downloading_section: 'downloading', aligning: 'aligning',
    cutting: 'cutting', done: '✓ done', error: 'error',
  }

  // ------------------------- loading -------------------------
  function pendingReport() {
    return !report || (report.video.status !== 'done' && report.video.status !== 'error')
  }

  async function loadReport() {
    try { report = await api.report(pid, lid); error = '' } catch (e) { error = e.message }
  }

  async function loadCuts() {
    try {
      cuts = await api.cuts(pid, lid)
      if (!activeCutId && cuts.length) activeCutId = cuts[0].id
      if (activeCutId) await loadBench()
    } catch (e) { error = e.message }
  }

  async function loadBench() {
    try { bench = await api.bench(pid, lid, activeCutId); cuts = bench.cuts } catch (e) { error = e.message }
  }

  async function loadKit() {
    try { kit = await api.projectDashboard(pid) } catch (_) { kit = null }
    try { pctx = await api.packagingContext(pid, lid) } catch (_) { pctx = null }
    try { project = await api.project(pid) } catch (_) { project = null }
  }

  onMount(() => {
    loadReport()
    loadCuts()
    loadKit()
    // While the pipeline runs, topics/transcript arrive over time; keep the
    // report and the bench (whose topics come from them) fresh.
    timer = setInterval(async () => {
      if (pendingReport()) { await loadReport(); await loadBench() }
      // While clips are still rendering, keep the composition fresh so the
      // per-item status advances (queued → downloading → aligning → cutting → done/error).
      else if (anyClipActive()) await loadBench()
      // While an export is being packaged, poll so the download link appears.
      else if (exportActive(bench && bench.export)) await loadBench()
      // While an AI packaging pass (titles/brief/frame ranking) or the audience
      // match is in flight, poll so the per-item results land in place.
      else if (cutPackagingActive(items) || audienceMatching()) await loadBench()
    }, 2000)
  })
  onDestroy(() => { clearInterval(timer); stopDriver(); clearTimeout(gapTimer) })

  // ------------------------- derived -------------------------
  $: running = report ? isActive(report.video.status) : false
  $: topics = bench ? bench.topics : []
  $: items = bench ? bench.items : []
  // The clip candidates for the Topics card — the live's topics and chat peaks
  // merged and sorted by their moment, exactly as the original card showed them.
  // The rich fields (summary, score, fire) come from the report; the mark state
  // is joined in from the bench by topic id.
  $: candidates = report
    ? [
        ...report.topics.map((t) => ({
          key: `t${t.id}`, kind: 'topic', id: t.id, start: t.start, title: t.title,
          time: `${t.start_hms}–${t.end_hms}`, meta: `score ${t.score.toFixed(2)}`,
          summary: t.summary, youtube_url: t.youtube_url, fire: t.fire,
        })),
        ...((report.chat_signal && report.chat_signal.peaks) || []).map((p, i) => ({
          key: `p${i}`, kind: 'peak', start: p.ts, title: 'Chat peak',
          time: p.hms, meta: 'chat spike', youtube_url: p.youtube_url,
        })),
      ].sort((a, b) => a.start - b.start)
    : []
  $: benchById = new Map(topics.map((t) => [t.id, t]))
  // Marked-state tally for the Topics header (in / maybe / open).
  $: counts = topics.reduce(
    (a, t) => { if (a[t.state] != null) a[t.state]++; return a },
    { in: 0, maybe: 0, open: 0, discarded: 0 }
  )
  $: segs = report
    ? report.transcript.filter((s) => !query || s.text.toLowerCase().includes(query.toLowerCase()))
    : []
  $: selectedTopic = topics.find((t) => t.id === selected) || null
  $: renderedCount = items.filter((it) => it.clip && it.clip.state === 'done').length
  $: failedCount = items.filter((it) => it.clip && it.clip.state === 'error').length
  $: exp = bench ? bench.export : null

  // The packaging kit, drawn from the niche's patterns (long-form default).
  $: patterns = (kit && kit.patterns && kit.patterns.long ? kit.patterns.long : [])
    .filter((p) => p.verdict === 'positive').slice(0, 5)
  $: refOutliers = (kit && kit.outliers && kit.outliers.long ? kit.outliers.long : []).slice(0, 3)
  $: asks = kit
    ? Object.values(kit.audience_requests.by_type || {}).flat().slice(0, 6).map((r) => r.value)
    : []
  const heat = (lift) => (lift >= 1.05 ? 'bg-heat-bg99 text-heat-99' : 'bg-card2 text-ink-3')

  // ------------------------- interactions -------------------------
  // Seek the player and bring it to the top of the page (project rule: every
  // clickable timestamp seeks and scrolls to the top).
  function seekTo(seconds, youtubeUrl) {
    const ok = playerRef && playerRef.seek(seconds)
    if (!ok) { window.open(youtubeUrl, '_blank', 'noreferrer'); return }
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  // Two independent actions per candidate (project rule): the title expands its
  // summary (topics that have one) or plays; the timestamp always plays. `play`
  // highlights the row, lands the transcript highlight for topics, and leaves
  // Preview mode so the source video is on screen.
  let selectedKey = null // highlighted row (topic or peak)
  let expanded = {} // key → bool
  const expandable = (it) => it.kind === 'topic' && !!it.summary
  function play(it) {
    selectedKey = it.key
    if (it.kind === 'topic') selected = it.id
    if (mode === 'preview') mode = 'source'
    seekTo(it.start, it.youtube_url)
  }
  function onTitle(it) {
    if (expandable(it)) expanded = { ...expanded, [it.key]: !expanded[it.key] }
    else play(it)
  }

  // The click cycle open -> in -> maybe -> open, writing to the right place per
  // transition: `in` adds/removes a cut item; `maybe` writes triage; `open` clears
  // both. `in` is never written to triage. A legacy `discarded` topic (indexOf -1)
  // cycles straight back to `open`.
  async function cycle(t) {
    const from = t.state
    const to = ORDER[(ORDER.indexOf(from) + 1) % ORDER.length]
    try {
      if (to === 'in') {
        bench = await api.addCutItem(pid, lid, activeCutId, { topic_id: t.id })
        cuts = bench.cuts
      } else {
        if (from === 'in') {
          const it = items.find((i) => i.topic_id === t.id)
          if (it) await api.removeCutItem(pid, lid, activeCutId, it.id)
        }
        await api.setTriage(pid, lid, t.id, to) // 'open' clears the row
        await loadBench()
      }
      error = ''
    } catch (e) { error = e.message }
  }

  async function removeItem(it) {
    try { bench = await api.removeCutItem(pid, lid, activeCutId, it.id); cuts = bench.cuts }
    catch (e) { error = e.message }
  }

  function parseMMSS(str) {
    const parts = String(str).trim().split(':').map((n) => parseInt(n, 10))
    if (parts.some((n) => isNaN(n))) return NaN
    return parts.reduce((acc, n) => acc * 60 + n, 0)
  }

  async function addManual() {
    const start_s = parseMMSS(manualStart)
    const end_s = parseMMSS(manualEnd)
    if (isNaN(start_s) || isNaN(end_s)) { error = 'Use M:SS or seconds for the range.'; return }
    try {
      bench = await api.addCutItem(pid, lid, activeCutId, { start_s, end_s })
      cuts = bench.cuts
      manualStart = ''; manualEnd = ''; manualOpen = false; error = ''
    } catch (e) { error = e.message }
  }

  async function newCut() {
    try {
      const c = await api.createCut(pid, lid, null)
      activeCutId = c.id
      await loadBench()
    } catch (e) { error = e.message }
  }
  async function switchCut(e) { activeCutId = Number(e.target.value); await loadBench() }

  // Drag reorder of the composition (transactional PATCH with the ordered ids).
  let dragFrom = null
  function onDrop(to) {
    if (dragFrom === null || dragFrom === to) { dragFrom = null; return }
    const order = items.map((i) => i.id)
    const [moved] = order.splice(dragFrom, 1)
    order.splice(to, 0, moved)
    dragFrom = null
    reorder(order)
  }
  async function reorder(order) {
    try { bench = await api.reorderCutItems(pid, lid, activeCutId, order); cuts = bench.cuts }
    catch (e) { error = e.message }
  }

  // ------------------------- rendering (background) -------------------------
  function anyClipActive() { return items.some((it) => clipActive(it.clip)) }

  // Render is non-blocking: fire the request, then let the 2s poll advance each
  // clip's status in place while the user keeps deciding.
  async function renderAll() {
    if (!items.length) return
    rendering = true
    try { await api.render(pid, lid, activeCutId, {}); await loadBench(); error = '' }
    catch (e) { error = e.message }
    finally { rendering = false }
  }
  async function regenerateClip(it) {
    try { await api.render(pid, lid, activeCutId, { item_id: it.id, regenerate: true }); await loadBench() }
    catch (e) { error = e.message }
  }
  async function deleteClip(it) {
    if (!it.clip) return
    try { await api.deleteClip(pid, lid, activeCutId, it.clip.id); await loadBench() }
    catch (e) { error = e.message }
  }

  // ------------------------- export (background) -------------------------
  // Non-blocking like render: fire the request, the 2s poll surfaces the download
  // link when the zip is ready. render_missing renders any un-rendered item first.
  async function exportCut() {
    if (!items.length) return
    exporting = true
    try {
      await api.export(pid, lid, activeCutId, { render_missing: true, upscale_1440p: upscale })
      await loadBench(); error = ''
    } catch (e) { error = e.message }
    finally { exporting = false }
  }

  // ------------------------- AI packaging (background, opt-in) -------------------------
  function audienceMatching() { return bench && bench.audience_match && bench.audience_match.state === 'running' }

  // Each pass is opt-in and fired for the WHOLE cut (one LLM call per clip,
  // isolated per item). The 2s poll advances each item's packaging in place.
  async function runPackaging(action, kind) {
    if (!items.length) return
    pkgBusy = kind
    try { await action(); await loadBench(); error = '' }
    catch (e) { error = e.message }
    finally { pkgBusy = '' }
  }
  const genTitles = () =>
    runPackaging(() => api.genTitles(pid, lid, activeCutId, { k: titlesK }), 'title')
  const genBrief = () =>
    runPackaging(() => api.genBrief(pid, lid, activeCutId, {}), 'thumb_brief')
  const extractFrames = () =>
    runPackaging(() => api.extractFrames(pid, lid, activeCutId, { count: framesCount }), 'frames')
  const rankFrames = () =>
    runPackaging(() => api.rankFrames(pid, lid, activeCutId, {}), 'frame_ranking')

  // The frame ranking's chosen best filename for an item (highlights the frame).
  function bestFrame(it) {
    const r = it.packaging && it.packaging.frame_ranking
    return r && r.state === 'done' && r.payload ? r.payload.best_filename : null
  }
  // Tooltip listing which audience requests a topic matched.
  function audienceTip(t) {
    const m = t.signals.audience_matches || []
    return 'Audience asked: ' + m.map((r) => `${r.value} (${r.type})`).join(', ')
  }

  async function matchAudience() {
    matchingAudience = true
    try { await api.matchAudience(pid, lid, {}); await loadBench(); error = '' }
    catch (e) { error = e.message }
    finally { matchingAudience = false }
  }

  // ------------------------- virtual preview -------------------------
  function stopDriver() { if (driver) { clearInterval(driver); driver = null } }

  // Show the gap overlay while the YouTube player seeks to `i`, then land on it.
  function goToItem(i) {
    if (!items[i]) return
    gapLabel = `item ${i + 1} →`
    clearTimeout(gapTimer)
    gapTimer = setTimeout(() => {
      previewIndex = i
      seekPlayerTo(items[i].start_s)
      gapLabel = null
    }, 600)
  }
  function seekPlayerTo(seconds) {
    if (playerRef) playerRef.seek(seconds)
  }

  function playCut() {
    if (!items.length) return
    previewPlaying = true
    goToItem(previewIndex)
    stopDriver()
    driver = setInterval(() => {
      if (!previewPlaying || gapLabel) return
      const it = items[previewIndex]
      if (!it || !playerRef) return
      if (playerRef.getTime() >= it.end_s - 0.15) {
        if (previewIndex + 1 < items.length) goToItem(previewIndex + 1)
        else pauseCut() // end of the composition
      }
    }, 200)
  }
  function pauseCut() {
    previewPlaying = false
    stopDriver()
    if (playerRef) playerRef.pause()
  }
  function jumpToSegment(i) {
    previewIndex = i
    goToItem(i)
  }

  function setMode(m) {
    if (m === mode) return
    if (m === 'source') pauseCut()
    mode = m
  }

  // Highlight the transcript line nearest the selected topic and reveal it.
  let trBody
  $: if (trBody && selectedTopic) {
    const on = trBody.querySelector('[data-on="1"]')
    if (on) on.scrollIntoView({ block: 'center' })
  }
  const near = (t) => selectedTopic && Math.abs(t - selectedTopic.start) < 40
</script>

<a href={href.lives(pid)} class="eyebrow cursor-pointer hover:text-ink-2 mb-1 block no-underline">‹ {project?.name || 'Lives'} / Lives</a>
<div class="flex items-center justify-between mb-1 gap-3">
  <div class="eyebrow text-accent">Cut room</div>
  {#if cuts.length}
    <div class="flex items-center gap-2">
      <select
        class="font-mono text-[11px] bg-sunk border border-line-strong rounded-md px-2 py-1 text-ink cursor-pointer"
        value={activeCutId}
        on:change={switchCut}
      >
        {#each cuts as c (c.id)}
          <option value={c.id}>{c.name} · {c.item_count} item{c.item_count === 1 ? '' : 's'}</option>
        {/each}
      </select>
      <button class="btn btn-xs" on:click={newCut}>＋ New cut</button>
    </div>
  {/if}
</div>
<h1 class="mb-5">{report?.video.title || lid}</h1>

{#if error}<p class="m text-err mb-4">{error}</p>{/if}

{#if !report}
  <p class="m">Loading…</p>
{:else}
  <!-- Three columns: STAGE (sticky) | topic bench (the scroll axis) | COMPOSITION
       (sticky). Above 1200px they sit side by side and the side columns stick to
       the top while the bench drives the page scroll; below, they stack. -->
  <div class="grid grid-cols-1 gap-5 items-start min-[1201px]:grid-cols-[minmax(0,1fr)_440px_360px]">

    <!-- ======================= COL 1 · STAGE ======================= -->
    <div class="min-[1201px]:sticky min-[1201px]:top-4 min-[1201px]:self-start min-[1201px]:flex min-[1201px]:flex-col min-[1201px]:h-[calc(100vh-8.5rem)]">
      <div class="flex items-center justify-between mb-2.5 shrink-0">
        <div class="inline-flex border border-line-strong rounded-md overflow-hidden">
          <button
            class="font-mono text-[11px] px-3 py-1.5 {mode === 'source' ? 'bg-accent-bg text-accent' : 'text-ink-2 hover:bg-card2'}"
            on:click={() => setMode('source')}
          >Source</button>
          <button
            class="font-mono text-[11px] px-3 py-1.5 {mode === 'preview' ? 'bg-accent-bg text-accent' : 'text-ink-2 hover:bg-card2'}"
            on:click={() => setMode('preview')}
          >Preview cut</button>
        </div>
      </div>

      <!-- Player + gap overlay (Preview cut buffering between items). -->
      <div class="relative shrink-0">
        <Player bind:this={playerRef} videoId={report.video.id} />
        {#if gapLabel}
          <div class="absolute inset-0 grid place-items-center bg-[rgba(10,12,14,0.82)] font-mono text-[14px] text-accent tracking-wide rounded-card pointer-events-none">
            {gapLabel}
          </div>
        {/if}
      </div>

      {#if mode === 'source'}
        <!-- The full transcript, filling the column and scrolling internally so the
             page doesn't move while you read it (clickable timestamps → seek). -->
        <div class="bg-card border border-line rounded-card overflow-hidden flex flex-col mt-2.5 max-[1200px]:h-[440px] min-[1201px]:flex-1 min-[1201px]:min-h-0">
          <div class="px-4 py-3 border-b border-line flex items-center gap-2 shrink-0">
            <span class="lbl text-accent">Transcript</span>
            {#if report.transcript.length}
              <span class="tag bg-card2 text-ink-2 border-line-strong">{report.transcript.length}</span>
              <input class="ml-auto max-w-[200px] text-[12px] py-1" type="text" placeholder="search the transcript…" bind:value={query} />
            {/if}
          </div>
          <div bind:this={trBody} class="px-4 py-3 flex-1 overflow-y-auto min-h-0 {SCROLL}">
            {#if report.transcript.length === 0}
              <p class="m">{running ? 'Processing — the transcript appears as the pipeline finishes.' : 'No transcript for this live.'}</p>
            {:else if segs.length === 0}
              <p class="m">No segment matches “{query}”.</p>
            {/if}
            {#each segs as s (s.index)}
              <p class="text-[13px] leading-relaxed mb-1.5" data-on={near(s.start) ? '1' : '0'}>
                <button type="button" class="font-mono text-[11px] {near(s.start) ? 'text-accent-hi' : 'text-accent'} hover:text-accent-hi cursor-pointer align-baseline mr-1" on:click={() => seekTo(s.start, s.youtube_url)}>{s.start_hms}</button>
                <span class="text-ink-2">{s.text}</span>
              </p>
            {/each}
          </div>
        </div>
      {:else}
        <!-- Preview cut: a segmented scrub (one segment per item, width ∝ its
             duration), play/pause the composition, and the honest gap note. -->
        <div class="mt-3">
          <div class="flex gap-[3px] h-7">
            {#each items as it, i (it.id)}
              <button
                type="button"
                class="rounded-[3px] flex items-center justify-center font-mono text-[9px] min-w-[10px] {i === previewIndex ? 'bg-accent-bg text-accent shadow-[inset_0_0_0_1px_theme(colors.accent.line)]' : 'bg-card2 text-ink-3 hover:bg-[#2a3138]'}"
                style="flex:{it.duration_s || 1}"
                on:click={() => jumpToSegment(i)}
              >{i + 1}</button>
            {/each}
            {#if !items.length}
              <div class="flex-1 grid place-items-center font-mono text-[10px] text-ink-3 bg-card2 rounded-[3px]">No items marked to cut yet.</div>
            {/if}
          </div>
          <div class="flex justify-between mt-1.5">
            <span class="m">composition · {items.length} item{items.length === 1 ? '' : 's'}</span>
            <span class="m">{hms(bench ? bench.total_duration_s : 0)}</span>
          </div>
        </div>
        <div class="flex items-center gap-2.5 mt-2.5">
          <button class="btn btn-pri" on:click={() => (previewPlaying ? pauseCut() : playCut())} disabled={!items.length}>
            {previewPlaying ? '❚❚ Pause' : '▶ Play cut'}
          </button>
          {#if items[previewIndex]}
            <span class="m">item {previewIndex + 1} of {items.length} · {items[previewIndex].title}</span>
          {/if}
        </div>
        <p class="font-mono text-[10px] text-ink-3 leading-relaxed mt-2.5">
          Seek gaps between items are the YouTube player buffering — shown as an overlay, not a glitch. The rendered clip (next step) is seamless.
        </p>
      {/if}
    </div>

    <!-- ======================= COL 2 · TOPICS ======================= -->
    <div class="min-[1201px]:sticky min-[1201px]:top-4 min-[1201px]:self-start">
      <!-- The Topics card fills the viewport height and scrolls internally (the
           Full HD fit rule), matching the player+transcript column's height. -->
      <div class="bg-card border border-line rounded-card overflow-hidden flex flex-col max-[1200px]:h-[440px] min-[1201px]:h-[calc(100vh-8.5rem)]">
        <div class="px-4 py-3 border-b border-line flex items-center gap-2 shrink-0">
          <span class="lbl text-accent">Topics</span>
          <span class="m ml-auto">{counts.in} in · {counts.maybe} maybe · {counts.open} open</span>
        </div>
        <div class="p-2 flex-1 overflow-y-auto min-h-0 {SCROLL}">
          {#if candidates.length === 0}
            <p class="m px-1.5 py-2">{running ? 'Processing — topics appear as the pipeline finishes.' : 'No topics yet. Rerun with topics to generate them.'}</p>
          {/if}
          {#each candidates as it (it.key)}
            {@const isOpen = !!expanded[it.key]}
            {@const bt = it.kind === 'topic' ? benchById.get(it.id) : null}
            <div class="topic" class:on={selectedKey === it.key}>
              <div class="flex items-start gap-2">
                <div class="flex-1 min-w-0">
                  <!-- Title expands/collapses the description (topics with a
                       summary); for the rest it plays. The timestamp below always
                       plays. -->
                  <button
                    type="button"
                    class="w-full text-left cursor-pointer"
                    aria-expanded={expandable(it) ? isOpen : undefined}
                    on:click={() => onTitle(it)}
                  >{it.kind === 'peak' ? '🔥 ' : ''}{it.title}{it.fire ? ' 🔥' : ''}</button>
                  <div class="m mt-0.5">
                    <button type="button" class="text-accent hover:text-accent-hi cursor-pointer" on:click={() => play(it)}>{it.time}</button>
                    <span class="text-ink-3"> · {it.meta}</span>
                  </div>
                </div>
                <!-- Mark toggle (topics only): cycle open → in → maybe → discarded,
                     writing the topic into the cut / triage. State shows in the icon. -->
                {#if bt}
                  <button
                    type="button"
                    class="shrink-0 w-5 text-[16.5px] leading-none text-center cursor-pointer {bt.state === 'in' || bt.state === 'maybe' ? 'text-accent' : 'text-ink-3 hover:text-ink'}"
                    title="mark: open → in → maybe"
                    aria-label="mark topic"
                    on:click|stopPropagation={() => cycle(bt)}
                  >{ICON[bt.state]}</button>
                {/if}
              </div>
              {#if isOpen && it.summary}
                <p class="text-ink-2 text-[12.5px] leading-relaxed mt-1.5">{it.summary}</p>
              {/if}
            </div>
          {/each}
        </div>
      </div>
    </div>

    <!-- ======================= COL 3 · COMPOSITION ======================= -->
    <div class="min-[1201px]:sticky min-[1201px]:top-4 min-[1201px]:self-start">
      <div class="bg-card border border-line rounded-card mb-2.5">
        <div class="px-4 py-3 border-b border-line flex items-center justify-between">
          <span class="lbl">Composition</span>
          <span class="m">{items.length} · {hms(bench ? bench.total_duration_s : 0)}</span>
        </div>
        <div class="px-3 py-2.5">
          {#if items.length === 0}
            <p class="font-mono text-[10px] text-ink-3 px-1">Mark topics to cut and they stack here.</p>
          {/if}
          {#each items as it, i (it.id)}
            <div
              class="py-1.5 {i > 0 ? 'border-t border-line' : ''}"
              draggable="true"
              on:dragstart={() => (dragFrom = i)}
              on:dragover|preventDefault
              on:drop|preventDefault={() => onDrop(i)}
              role="listitem"
            >
              <div class="flex gap-2 items-center">
                <span class="font-mono text-[9.5px] text-ink-3 w-3 cursor-grab">{i + 1}</span>
                <button type="button" class="flex-1 text-left text-[11.5px] truncate hover:text-accent" on:click={() => seekTo(it.start_s, `https://www.youtube.com/watch?v=${lid}&t=${Math.floor(it.start_s)}`)}>{it.title}</button>
                <span class="font-mono text-[9.5px] text-ink-3">{hms(it.duration_s)}</span>
                <button type="button" class="text-ink-3 hover:text-ink cursor-pointer text-[13px]" on:click={() => removeItem(it)} title="remove">✕</button>
              </div>
              {#if it.clip}
                <!-- Per-item render status: advances in place while rendering runs
                     in the background. Failure is loud (red); done offers download. -->
                <div class="flex gap-2 items-center mt-1 ml-5">
                  {#if it.clip.state === 'done'}
                    <a href={it.clip.download_url} class="tag bg-accent-bg text-accent border-accent-line no-underline" download>↓ {CLIP_LABEL.done}</a>
                    <button type="button" class="font-mono text-[9.5px] text-ink-3 hover:text-ink" on:click={() => regenerateClip(it)} title="re-render">↻</button>
                    <button type="button" class="font-mono text-[9.5px] text-ink-3 hover:text-err" on:click={() => deleteClip(it)} title="delete clip">🗑</button>
                  {:else if it.clip.state === 'error'}
                    <span class="tag bg-err-bg text-err border-err-line" title={it.clip.error}>render failed</span>
                    <button type="button" class="font-mono text-[9.5px] text-ink-3 hover:text-ink" on:click={() => regenerateClip(it)} title="retry">↻ retry</button>
                  {:else}
                    <span class="tag bg-card2 text-ink-2 border-line-strong">
                      <span class="inline-block w-1.5 h-1.5 rounded-full bg-accent animate-pulse mr-1"></span>{CLIP_LABEL[it.clip.state] || it.clip.state}…
                    </span>
                  {/if}
                </div>
              {/if}
            </div>
          {/each}

          {#if manualOpen}
            <div class="flex gap-1.5 items-center mt-2.5 pt-2.5 border-t border-line">
              <input class="text-[11px] py-1 px-2" placeholder="0:00" bind:value={manualStart} />
              <span class="m">→</span>
              <input class="text-[11px] py-1 px-2" placeholder="0:30" bind:value={manualEnd} />
              <button class="btn btn-xs btn-pri" on:click={addManual}>Add</button>
            </div>
          {:else}
            <button class="btn btn-xs w-full mt-2.5" on:click={() => (manualOpen = true)}>＋ Add manual range</button>
          {/if}
        </div>
      </div>

      <button class="btn btn-pri w-full" disabled={!items.length || rendering} on:click={renderAll}>
        {rendering ? 'Rendering…' : `Render ${items.length} clip${items.length === 1 ? '' : 's'}`}
      </button>
      <p class="font-mono text-[10px] text-ink-3 mt-2 leading-relaxed">
        {#if renderedCount}
          {renderedCount} of {items.length} rendered{failedCount ? ` · ${failedCount} failed` : ''}. Clips are deliverables — download or delete them; nothing is cleaned up automatically.
        {:else}
          Renders in the background (NVENC, exact cuts) — keep deciding while clips are produced. Each item becomes its own MP4.
        {/if}
      </p>

      <!-- Export: package the whole cut (clips + SRT + master + manifests) into a
           zip. render_missing renders any un-rendered item first, so it works even
           before an explicit Render. -->
      <div class="flex gap-2 mt-3">
        <button class="btn btn-pri flex-1" disabled={!items.length || exporting || exportActive(exp)} on:click={exportCut}>
          {exporting || exportActive(exp) ? 'Exporting…' : 'Export package'}
        </button>
        {#if exp && exp.state === 'done' && exp.download_url}
          <a class="btn" href={exp.download_url} download>↓ Download zip</a>
        {/if}
      </div>
      <label class="flex items-center gap-2 mt-2 font-mono text-[10px] text-ink-3 cursor-pointer">
        <input type="checkbox" bind:checked={upscale} />
        Upscale master to 1440p (NVENC — better YouTube codecs)
      </label>
      <p class="font-mono text-[10px] text-ink-3 mt-1 leading-relaxed">
        {#if exp && exp.state === 'error'}
          <span class="text-err">Export failed: {exp.error}</span>
        {:else if exp && exp.state === 'done'}
          Packaged {exp.item_count} clip{exp.item_count === 1 ? '' : 's'}{exp.upscaled ? ' · master 1440p' : ''}. The zip holds NNN-slug.mp4/.srt, master.mp4 and manifests.
        {:else if exportActive(exp)}
          Packaging in the background (concat master, no re-encode) — the download appears when the zip is ready.
        {:else}
          Copies the clips (originals untouched) + per-clip SRT + a concat master + manifests into a zip.
        {/if}
      </p>

      <!-- Packaging kit: AI packaging of the cut (opt-in, per clip) + the niche
           patterns that drive it. Collapsed by default. -->
      <button class="btn w-full mt-3 flex justify-between" on:click={() => (showKit = !showKit)}>
        <span>Packaging kit</span><span class="text-ink-3">{showKit ? '▾' : '▸'}</span>
      </button>
      {#if showKit}
        <div class="bg-card border border-line rounded-card mt-2 p-4">
          {#if pctx && !pctx.available}
            <p class="m leading-relaxed">Packaging is unavailable: {pctx.reason}.
              <a class="text-accent" href={href.settings(pid)}>Open project settings</a> to draw niche patterns.</p>
          {:else}
            {#if pctx && pctx.flat}
              <p class="m mb-3 leading-relaxed text-ink-3">This niche is <b>flat</b> on packaging — no attribute separates the winners. The brief will honestly say packaging is not the lever here and lean on the subject and the value that carries.</p>
            {/if}

            <!-- Generation actions. Each LLM pass (teal) costs money; frame
                 extraction is free/deterministic. Every pass runs per clip. -->
            <div class="lbl mb-1.5">Generate (per clip)</div>
            <div class="flex flex-wrap gap-2 items-center">
              <label class="flex items-center gap-1 font-mono text-[10px] text-ink-3">K
                <input type="number" min="1" max="10" bind:value={titlesK} class="w-12 text-[11px] py-0.5 px-1" /></label>
              <button class="btn btn-xs btn-ai" disabled={!items.length || pkgBusy === 'title'} on:click={genTitles}>
                {pkgBusy === 'title' ? 'Generating…' : '✨ Titles'}</button>
              <button class="btn btn-xs btn-ai" disabled={!items.length || pkgBusy === 'thumb_brief'} on:click={genBrief}>
                {pkgBusy === 'thumb_brief' ? 'Generating…' : '✨ Thumbnail brief'}</button>
            </div>
            <div class="flex flex-wrap gap-2 items-center mt-2">
              <label class="flex items-center gap-1 font-mono text-[10px] text-ink-3">frames
                <input type="number" min="1" max="10" bind:value={framesCount} class="w-12 text-[11px] py-0.5 px-1" /></label>
              <button class="btn btn-xs" disabled={!items.length || pkgBusy === 'frames'} on:click={extractFrames}>
                {pkgBusy === 'frames' ? 'Extracting…' : 'Extract frames'}</button>
              <button class="btn btn-xs btn-ai" disabled={!items.length || pkgBusy === 'frame_ranking'} on:click={rankFrames}>
                {pkgBusy === 'frame_ranking' ? 'Ranking…' : '✨ Rank frames'}</button>
            </div>
            <p class="font-mono text-[10px] text-ink-3 mt-1.5 leading-relaxed">Frame extraction is free (FFmpeg). Titles, brief and ranking each cost an LLM call. Results ride into the export manifest.</p>

            <!-- Per-clip results. -->
            {#each items as it (it.id)}
              {#if it.packaging && (it.packaging.title || it.packaging.thumb_brief || it.packaging.frames.length)}
                <div class="border-t border-line mt-3 pt-2.5">
                  <div class="font-mono text-[10px] text-ink-3 mb-1">{it.title}</div>
                  {#if it.packaging.title}
                    {#if packagingActive(it.packaging.title)}
                      <p class="m">Proposing titles…</p>
                    {:else if it.packaging.title.state === 'error'}
                      <p class="text-err text-[11px]">Titles failed: {it.packaging.title.error}</p>
                    {:else}
                      {#each it.packaging.title.payload.titles as t}
                        <div class="text-[12.5px] text-ink leading-snug mb-1">
                          {t.title}
                          {#each t.patterns_applied as pa}
                            <span class="font-mono text-[9px] px-1 py-px rounded bg-card2 text-ink-3 ml-1">{pa.reference}</span>
                          {/each}
                        </div>
                      {/each}
                    {/if}
                  {/if}
                  {#if it.packaging.thumb_brief}
                    {#if packagingActive(it.packaging.thumb_brief)}
                      <p class="m mt-1">Writing the thumbnail brief…</p>
                    {:else if it.packaging.thumb_brief.state === 'error'}
                      <p class="text-err text-[11px] mt-1">Brief failed: {it.packaging.thumb_brief.error}</p>
                    {:else}
                      <div class="text-[12px] text-ink-2 mt-1 leading-snug">
                        <span class="lbl">Thumb</span>
                        {it.packaging.thumb_brief.payload.composition.face_count} face(s), {it.packaging.thumb_brief.payload.composition.expression}, {it.packaging.thumb_brief.payload.composition.framing} · text: “{it.packaging.thumb_brief.payload.text.suggestion}”
                      </div>
                    {/if}
                  {/if}
                  {#if it.packaging.frames.length}
                    <div class="flex gap-1.5 mt-1.5 flex-wrap">
                      {#each it.packaging.frames as f}
                        <img src={f.url} alt={f.name} class="h-12 rounded border {bestFrame(it) === f.name ? 'border-accent' : 'border-line'}" />
                      {/each}
                    </div>
                  {/if}
                </div>
              {/if}
            {/each}

            <!-- Read-only niche reference (what drives the generation above). -->
            <div class="lbl mt-5 mb-1">Niche patterns (reference)</div>
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
                <a href={href.video(pid, o.channel_id, o.video_id)} class="flex gap-2.5 py-1.5 border-t border-line text-[12px] text-ink-2 leading-snug no-underline hover:text-ink">
                  <span class="font-mono text-[11px] text-heat-99 shrink-0">{multiplier(o.median_multiple)}</span>
                  <span>{o.title || o.video_id}</span>
                </a>
              {/each}
            {/if}
            {#if asks.length}
              <div class="flex items-center justify-between mt-5 mb-1">
                <span class="lbl">The audience asked for</span>
                <button class="btn btn-xs btn-ai" disabled={matchingAudience || audienceMatching()} on:click={matchAudience}>
                  {matchingAudience || audienceMatching() ? 'Matching…' : '✨ Match to topics'}</button>
              </div>
              <p class="text-ink-2 text-[12.5px]">{asks.join(', ')}</p>
              <p class="font-mono text-[10px] text-ink-3 mt-1 leading-relaxed">Match tags each topic the niche explicitly asked for with an <b>audience asked</b> pill on the bench.</p>
            {/if}
          {/if}
        </div>
      {/if}
    </div>
  </div>
{/if}
