<script>
  import { onDestroy, onMount } from 'svelte'
  import { api, enrichActive, youtubeWatch } from '../lib/api.js'
  import Sparkline from '../components/Sparkline.svelte'
  import { compactNumber, pct, vphLabel, multiplier, shortDate, hms } from '../lib/format.js'

  export let pid
  export let cid
  export let vid

  let data = null // null while loading
  let error = ''
  let timer = null

  async function load() {
    try {
      data = await api.competitorVideo(cid, vid)
      error = ''
    } catch (e) {
      error = e.message
    }
  }
  onMount(load)
  onDestroy(() => clearInterval(timer))

  // The enrichment runs on the queue, so poll while a call is in flight — and
  // stop as soon as none is, like the channel backfill polling. The comment
  // synthesis carries the same state envelope, so it polls with the same predicate.
  $: running =
    data &&
    (['title_llm', 'thumbnail_llm'].some((s) => enrichActive(data.attributes[s])) ||
      enrichActive(data.comment_synthesis))
  $: if (!running) {
    clearInterval(timer)
    timer = null
  }

  async function enrich(kind, regenerate = false) {
    try {
      await api.enrichVideo(cid, vid, kind, regenerate)
      await load()
      if (!timer) timer = setInterval(load, 2000)
    } catch (e) {
      error = e.message
    }
  }

  $: views = data ? data.snapshots.map((s) => s.views) : []
  $: syntactic = data && data.attributes.syntactic
  $: titleAi = data && data.attributes.title_llm
  $: thumbAi = data && data.attributes.thumbnail_llm
  $: synth = data && data.comment_synthesis
  // Comments are collected automatically, but only for definitive outliers — so
  // "none yet" is the normal state for most videos, not a failure.
  $: comments = data && data.comments
  $: canSynthesise = comments && comments.collected > 0

  // The derived boolean facets (thumb_*/title_*) exist for the pattern
  // distributions; what a human wants to see here is the raw observation.
  const RAW = ([k]) => !k.startsWith('thumb_') && !k.startsWith('title_')
  const rawEntries = (attrs) => (attrs ? Object.entries(attrs.values).filter(RAW) : [])

  const fmt = (v) => {
    if (typeof v === 'boolean') return v ? '✓' : '✗'
    if (Array.isArray(v)) return v.length ? v.join(', ') : '—'
    return v === '' ? '—' : v
  }
</script>

<p><a class="mini link" href={`#/projects/${pid}/channels/${cid}`}>‹ back to channel</a></p>

{#if error}<p class="error">{error}</p>{/if}

{#if !data}
  <p class="muted">Loading…</p>
{:else}
  <header class="vd-head">
    {#if data.video.thumbnail_url}
      <img class="vd-thumb" src={data.video.thumbnail_url} alt="" />
    {/if}
    <div class="grow">
      <h2>{data.video.title || data.video.video_id}</h2>
      <p class="muted">
        {shortDate(data.video.published_at) || 'unknown date'} ·
        {data.video.is_short ? 'short' : 'long-form'}
        {#if data.video.duration_s} · {hms(data.video.duration_s)}{/if}
      </p>
      <a class="mini" href={youtubeWatch(data.video.video_id)} target="_blank" rel="noreferrer">
        ↗ Watch on YouTube
      </a>
    </div>
  </header>

  <section class="tiles">
    <article class="card tile">
      <h3>Outlier</h3>
      {#if data.outlier.status === 'insufficient_data'}
        <p class="muted">Insufficient data — fewer than the minimum same-format peers to score against.</p>
      {:else}
        <div class="tile-row">
          <span>Score</span>
          <b class="mono">
            {multiplier(data.outlier.score)}
            {#if data.outlier.bracket}<span class="badge st-done">≥{data.outlier.bracket}</span>{/if}
            {#if data.outlier.provisional}<span class="status status-provisional">provisional</span>{/if}
          </b>
        </div>
        <div class="tile-row">
          <span>Baseline ({data.outlier.format})</span>
          <b class="mono">{compactNumber(data.outlier.baseline)} <span class="muted">(n={data.outlier.sample_size})</span></b>
        </div>
      {/if}
    </article>

    <article class="card tile">
      <h3>Velocity</h3>
      <div class="tile-row"><span>Current VPH</span><b class="mono">{vphLabel(data.vph.current)}</b></div>
      <div class="tile-row"><span>Like rate</span><b class="mono">{pct(data.engagement.like_rate)}</b></div>
      <div class="tile-row"><span>Comment rate</span><b class="mono">{pct(data.engagement.comment_rate)}</b></div>
      {#if data.resurgence.is_resurgent}
        <p class="collecting">
          Resurging: {multiplier(data.resurgence.ratio)} its recent average
          ({vphLabel(data.resurgence.current_vph)} vs {vphLabel(data.resurgence.recent_avg_vph)}).
        </p>
      {/if}
    </article>
  </section>

  <section>
    <h2>Views over time</h2>
    {#if data.snapshots.length === 0}
      <p class="collecting">Collecting data — provisional scores from tomorrow.</p>
    {:else}
      <Sparkline values={views} />
      <p class="muted">
        {data.snapshots.length} {data.snapshots.length === 1 ? 'snapshot' : 'snapshots'} ·
        {shortDate(data.snapshots[0].captured_at)} → {shortDate(data.snapshots[data.snapshots.length - 1].captured_at)} ·
        latest {compactNumber(data.snapshots[data.snapshots.length - 1].views)} views
      </p>
    {/if}
  </section>

  <section>
    <h2>Title attributes</h2>
    {#if rawEntries(syntactic).length === 0}
      <p class="muted">No syntactic attributes recorded yet — run <code>primocut metrics refresh</code>.</p>
    {:else}
      <ul class="attr-list">
        {#each rawEntries(syntactic) as [k, v] (k)}
          <li class="attr"><span class="muted">{k}</span> <b class="mono">{fmt(v)}</b></li>
        {/each}
      </ul>
    {/if}
  </section>

  <section>
    <div class="tab-bar flush">
      <h2 class="grow">Thumbnail attributes (AI)</h2>
      <button
        class="mini ai tab-bar-action"
        disabled={enrichActive(thumbAi)}
        on:click={() => enrich('thumbnail')}
      >
        ✨ {thumbAi && thumbAi.state === 'done' ? 'Re-analyse' : 'Analyse'} thumbnail with AI
      </button>
    </div>
    {#if enrichActive(thumbAi)}
      <p class="processing">Analysing the thumbnail…</p>
    {:else if thumbAi && thumbAi.state === 'error'}
      <p class="error">Thumbnail analysis failed: {thumbAi.error}</p>
    {:else if !thumbAi}
      <p class="muted">Not analysed yet. This calls a vision model and costs money, so it only runs when you ask.</p>
    {:else}
      <ul class="attr-list">
        {#each rawEntries(thumbAi) as [k, v] (k)}
          <li class="attr"><span class="muted">{k}</span> <b class="mono">{fmt(v)}</b></li>
        {/each}
      </ul>
      <p class="muted">Analysed {shortDate(thumbAi.updated_at)} · re-runs only if the thumbnail image changes.</p>
    {/if}
  </section>

  <section>
    <div class="tab-bar flush">
      <h2 class="grow">Title semantics (AI)</h2>
      <button
        class="mini ai tab-bar-action"
        disabled={enrichActive(titleAi)}
        on:click={() => enrich('title')}
      >
        ✨ {titleAi && titleAi.state === 'done' ? 'Re-analyse' : 'Analyse'} title with AI
      </button>
    </div>
    {#if enrichActive(titleAi)}
      <p class="processing">Analysing the title…</p>
    {:else if titleAi && titleAi.state === 'error'}
      <p class="error">Title analysis failed: {titleAi.error}</p>
    {:else if !titleAi}
      <p class="muted">Not analysed yet. This calls an LLM and costs money, so it only runs when you ask.</p>
    {:else}
      <ul class="attr-list">
        {#each rawEntries(titleAi) as [k, v] (k)}
          <li class="attr"><span class="muted">{k}</span> <b class="mono">{fmt(v)}</b></li>
        {/each}
      </ul>
      <p class="muted">Analysed {shortDate(titleAi.updated_at)} · re-runs only if the title changes.</p>
    {/if}
  </section>

  <section>
    <div class="tab-bar flush">
      <h2 class="grow">Audience comments</h2>
      {#if canSynthesise}
        <button
          class="mini ai tab-bar-action"
          disabled={enrichActive(synth)}
          on:click={() => enrich('comments')}
        >
          ✨ {synth && synth.state === 'done' ? 'Re-synthesise' : 'Synthesise'} comments with AI
        </button>
        {#if synth && synth.state === 'done'}
          <button class="mini" disabled={enrichActive(synth)} on:click={() => enrich('comments', true)}>
            Regenerate
          </button>
        {/if}
      {/if}
    </div>

    {#if comments.state === 'disabled'}
      <p class="muted">Comments are disabled on this video — there is nothing to collect.</p>
    {:else if comments.state === 'error'}
      <p class="error">Comment collection failed: {comments.error} — it retries on the next cycle.</p>
    {:else if comments.state === 'done' && !canSynthesise}
      <p class="muted">Nobody commented on this video — there is nothing to synthesise.</p>
    {:else if !canSynthesise}
      <p class="muted">
        No comments collected yet. They are fetched automatically once the video becomes a
        definitive outlier.
      </p>
    {:else}
      <p class="muted">
        {comments.collected} top {comments.collected === 1 ? 'comment' : 'comments'} collected
        automatically (no AI cost).
      </p>

      {#if enrichActive(synth)}
        <p class="processing">Synthesising the comments…</p>
      {:else if synth && synth.state === 'error'}
        <p class="error">Comment synthesis failed: {synth.error}</p>
      {:else if !synth}
        <p class="muted">
          Not synthesised yet. This calls an LLM and costs money, so it only runs when you ask.
        </p>
      {:else}
        <p>
          <b>Sentiment:</b> <span class="badge st-done">{synth.values.sentiment}</span>
          {synth.values.sentiment_note}
        </p>

        <h3>Recurring themes</h3>
        {#if synth.values.themes.length === 0}
          <p class="muted">No recurring theme identified.</p>
        {:else}
          <ul class="list">
            {#each synth.values.themes as t (t.theme)}
              <li class="row">
                <div class="grow">
                  <b>{t.theme}</b>
                  {#if t.example && t.example.text}
                    <div class="muted">“{t.example.text}” — {t.example.author}</div>
                  {/if}
                </div>
                <span class="mono muted">{t.mentions}×</span>
              </li>
            {/each}
          </ul>
        {/if}

        <h3>What the audience asked for</h3>
        {#if synth.values.requests.length === 0}
          <p class="muted">No explicit request in these comments.</p>
        {:else}
          <ul class="list">
            {#each synth.values.requests as r (r.type + r.value_key)}
              <li class="row">
                <div class="grow">
                  <span class="tag">{r.type}</span> <b>{r.value}</b>
                  {#if r.example && r.example.text}
                    <div class="muted">“{r.example.text}” — {r.example.author}</div>
                  {/if}
                </div>
                <span class="mono muted">{r.mentions}×</span>
              </li>
            {/each}
          </ul>
        {/if}

        <p class="muted">
          Synthesised {shortDate(synth.updated_at)} over {synth.values.comments_analysed} comments ·
          re-runs only if the collected comments change. Every quote is a real comment.
        </p>
      {/if}
    {/if}
  </section>

  <section>
    <h2>Packaging changes</h2>
    {#if data.packaging_changes.length === 0}
      <p class="muted">No packaging change detected yet.</p>
    {:else}
      <ul class="list">
        {#each data.packaging_changes as c (c.field + c.detected_at)}
          <li class="row">
            <div class="grow">
              <b>{c.field}</b> changed {shortDate(c.detected_at)}
              {#if c.field === 'title'}
                <div class="muted">“{c.old_value}” → “{c.new_value}”</div>
              {/if}
            </div>
            <span class="mono muted">{vphLabel(c.vph_before)} → {vphLabel(c.vph_after)}</span>
          </li>
        {/each}
      </ul>
    {/if}
  </section>
{/if}
