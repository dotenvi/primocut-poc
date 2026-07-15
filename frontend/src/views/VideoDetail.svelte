<script>
  import { onMount } from 'svelte'
  import { api, youtubeWatch } from '../lib/api.js'
  import Sparkline from '../components/Sparkline.svelte'
  import { compactNumber, pct, vphLabel, multiplier, shortDate, hms } from '../lib/format.js'

  export let pid
  export let cid
  export let vid

  let data = null // null while loading
  let error = ''

  async function load() {
    try {
      data = await api.competitorVideo(cid, vid)
      error = ''
    } catch (e) {
      error = e.message
    }
  }
  onMount(load)

  $: views = data ? data.snapshots.map((s) => s.views) : []
  // Syntactic attributes are a flat object; `source` is metadata, not a feature.
  $: attributes = data && data.attributes
    ? Object.entries(data.attributes).filter(([k]) => k !== 'source')
    : []

  const fmt = (v) => (typeof v === 'boolean' ? (v ? '✓' : '✗') : v)
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
    {#if attributes.length === 0}
      <p class="muted">No syntactic attributes recorded yet — run <code>primocut metrics refresh</code>.</p>
    {:else}
      <ul class="attr-list">
        {#each attributes as [k, v] (k)}
          <li class="attr"><span class="muted">{k}</span> <b class="mono">{fmt(v)}</b></li>
        {/each}
      </ul>
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
