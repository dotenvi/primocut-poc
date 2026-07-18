<script>
  import { onDestroy, onMount } from 'svelte'
  import { api, enrichActive, youtubeWatch } from '../lib/api.js'
  import { href, goto } from '../lib/router.js'
  import { compactNumber, pct, vphLabel, multiplier, shortDate, hms } from '../lib/format.js'
  import DistributionStrip from '../components/DistributionStrip.svelte'

  export let pid
  export let cid
  export let vid

  let data = null
  let dist = null
  let error = ''
  let timer = null

  async function load() {
    try { data = await api.competitorVideo(cid, vid); error = '' } catch (e) { error = e.message }
  }
  async function loadDist() {
    if (!data) return
    const fmt = data.video.is_short ? 'short' : 'long'
    try { dist = await api.distribution(cid, fmt) } catch (_) { dist = null }
  }
  onMount(async () => { await load(); await loadDist() })
  onDestroy(() => clearInterval(timer))

  $: running =
    data &&
    (['title_llm', 'thumbnail_llm'].some((s) => enrichActive(data.attributes[s])) ||
      enrichActive(data.comment_synthesis))
  $: if (!running && timer) { clearInterval(timer); timer = null }

  async function enrich(kind, regenerate = false) {
    try {
      await api.enrichVideo(cid, vid, kind, regenerate)
      await load()
      if (!timer) timer = setInterval(load, 2000)
    } catch (e) { error = e.message }
  }

  const RAW = ([k]) => !k.startsWith('thumb_') && !k.startsWith('title_')
  const rawEntries = (attrs) => (attrs ? Object.entries(attrs.values).filter(RAW) : [])
  const fmtVal = (v) => {
    if (typeof v === 'boolean') return v ? '✓' : '✗'
    if (Array.isArray(v)) return v.length ? v.join(', ') : '—'
    return v === '' ? '—' : v
  }

  $: thumbAi = data && data.attributes.thumbnail_llm
  $: titleAi = data && data.attributes.title_llm
  $: synth = data && data.comment_synthesis
  $: comments = data && data.comments
  $: canSynthesise = comments && comments.collected > 0
  $: latestViews = data && data.snapshots.length ? data.snapshots[data.snapshots.length - 1].views : null
</script>

<a href={href.channel(pid, cid)} class="eyebrow cursor-pointer hover:text-ink-2 mb-2 block no-underline">‹ Niche / channel</a>

{#if error}<p class="m text-err mb-4">{error}</p>{/if}

{#if !data}
  <p class="m">Loading…</p>
{:else}
  <h1 class="mb-5 max-w-[820px]">{data.video.title || data.video.video_id}</h1>

  <div class="grid grid-cols-2 gap-4 mb-5 max-[960px]:grid-cols-1">
    <div class="card">
      <div class="flex items-start justify-between gap-4">
        <div class="flex-1">
          <div class="lbl mb-2">Standing</div>
          {#if data.outlier.status === 'insufficient_sample'}
            <p class="m">Insufficient same-format peers to score against.</p>
          {:else}
            <div class="kv"><span>Band</span>
              {#if data.outlier.band}
                <span class="tag bg-heat-bg99 text-heat-99 border-heat-ln99">{data.outlier.percentile ? `p${data.outlier.percentile} ` : ''}{data.outlier.band}</span>
              {:else}<span class="m">—</span>{/if}
            </div>
            <div class="kv"><span>Multiple of median</span><span class="font-mono text-[12px] tabular-nums">{multiplier(data.outlier.median_multiple)}</span></div>
            <div class="kv"><span>Channel median · {data.outlier.format}</span><span class="font-mono text-[12px] tabular-nums">{compactNumber(data.outlier.baseline)} · n={data.outlier.sample_size}</span></div>
            <div class="kv"><span>Views</span><span class="font-mono text-[12px] tabular-nums">{compactNumber(latestViews)}</span></div>
          {/if}
        </div>
        {#if dist}
          <div class="pt-6">
            <DistributionStrip
              buckets={dist.buckets}
              edges={dist.bucket_edges}
              markViews={latestViews}
              height={52}
              label={`LAST ${dist.count} · ${data.video.is_short ? 'SHORT' : 'LONG'}`}
            />
          </div>
        {/if}
      </div>
    </div>

    <div class="card">
      <div class="lbl mb-2">Velocity</div>
      <div class="kv"><span>Current VPH</span><span class="font-mono text-[12px] tabular-nums">{vphLabel(data.vph.current)}</span></div>
      <div class="kv"><span>Like rate</span><span class="font-mono text-[12px] tabular-nums">{pct(data.engagement.like_rate)}</span></div>
      <div class="kv"><span>Comment rate</span><span class="font-mono text-[12px] tabular-nums">{pct(data.engagement.comment_rate)}</span></div>
      <div class="kv"><span>Published</span><span class="font-mono text-[12px] tabular-nums">{shortDate(data.video.published_at) || '—'}</span></div>
      {#if data.resurgence.is_resurgent}
        <p class="m text-accent mt-2">Resurging: {multiplier(data.resurgence.ratio)} its recent average.</p>
      {/if}
    </div>
  </div>

  {#if rawEntries(thumbAi).length || rawEntries(titleAi).length}
    <div class="lbl mb-2">Enriched attributes</div>
    <div class="card mb-5 max-w-[640px]">
      {#each [...rawEntries(titleAi), ...rawEntries(thumbAi)] as [k, v] (k)}
        <div class="pat"><span class="flex-1">{k}</span><span class="font-mono text-[11px] text-ink-2">{fmtVal(v)}</span></div>
      {/each}
    </div>
  {/if}

  {#if synth && synth.state === 'done' && synth.values.requests?.length}
    <div class="lbl mb-2">The audience asked for</div>
    <div class="card mb-5 max-w-[640px]">
      {#each synth.values.requests as r (r.type + r.value_key)}
        <div class="pat"><span class="flex-1"><span class="tag bg-card2 text-ink-2 border-line-strong mr-1.5">{r.type}</span>{r.value}</span><span class="font-mono text-[11px] text-ink-3">{r.mentions}×</span></div>
      {/each}
    </div>
  {/if}

  <div class="flex gap-1.5 flex-wrap">
    <button class="btn btn-ai" disabled={enrichActive(thumbAi)} on:click={() => enrich('thumbnail')}>
      {enrichActive(thumbAi) ? 'Analysing…' : thumbAi && thumbAi.state === 'done' ? 'Re-analyse thumbnail' : 'Analyse thumbnail'}
    </button>
    <button class="btn btn-ai" disabled={enrichActive(titleAi)} on:click={() => enrich('title')}>
      {enrichActive(titleAi) ? 'Analysing…' : titleAi && titleAi.state === 'done' ? 'Re-analyse title' : 'Analyse title'}
    </button>
    {#if canSynthesise}
      <button class="btn btn-ai" disabled={enrichActive(synth)} on:click={() => enrich('comments')}>
        {enrichActive(synth) ? 'Synthesising…' : synth && synth.state === 'done' ? 'Re-synthesise comments' : 'Synthesise comments'}
      </button>
    {/if}
    <a class="btn no-underline" href={youtubeWatch(data.video.video_id)} target="_blank" rel="noreferrer">Open on YouTube ↗</a>
  </div>

  {#if data.packaging_changes.length}
    <div class="lbl mt-7 mb-2">Packaging changes</div>
    <div class="card max-w-[640px]">
      {#each data.packaging_changes as c (c.field + c.detected_at)}
        <div class="kv"><span>{c.field} changed {shortDate(c.detected_at)}</span><span class="m">{vphLabel(c.vph_before)} → {vphLabel(c.vph_after)}</span></div>
      {/each}
    </div>
  {/if}
{/if}
