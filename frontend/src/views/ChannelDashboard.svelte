<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import OutlierTable from '../components/OutlierTable.svelte'
  import FormatTabs from '../components/FormatTabs.svelte'
  import { compactNumber, pct, vphLabel, multiplier, shortDate, WEEKDAYS } from '../lib/format.js'

  export let pid
  export let cid

  let data = null // null while loading
  let error = ''
  let outlierFmt = 'long'
  let resurgenceFmt = 'long'

  async function load() {
    try {
      data = await api.channelDashboard(cid)
      error = ''
    } catch (e) {
      error = e.message
    }
  }
  onMount(load)

  // No baseline in either format ⇒ too few videos/snapshots to say anything yet.
  $: collecting = data && !data.baselines.long && !data.baselines.short

  // The backend keys the distributions by stringified index; fill the gaps so the
  // histogram shows every weekday / hour, not only the ones with uploads.
  $: weekdayBars = data
    ? WEEKDAYS.map((label, i) => ({ label, count: data.cadence.by_weekday[String(i)] || 0 }))
    : []
  // 24 columns are too narrow to label individually — label every 6th hour.
  $: hourBars = data
    ? Array.from({ length: 24 }, (_, i) => ({
        key: String(i),
        label: i % 6 === 0 ? String(i) : '',
        title: `${i}h: ${data.cadence.by_hour[String(i)] || 0} uploads`,
        count: data.cadence.by_hour[String(i)] || 0,
      }))
    : []
  $: maxWeekday = Math.max(1, ...weekdayBars.map((b) => b.count))
  $: maxHour = Math.max(1, ...hourBars.map((b) => b.count))

  const videoHref = (r) => `#/projects/${pid}/channels/${cid}/videos/${r.video_id}`

  function verdict(p) {
    if (p.delta == null) return { label: 'impact window not elapsed', cls: '' }
    if (p.delta > 0) return { label: `▲ +${vphLabel(p.delta)}`, cls: 'st-done' }
    if (p.delta < 0) return { label: `▼ ${vphLabel(p.delta)}`, cls: 'st-failed' }
    return { label: 'no change', cls: '' }
  }
</script>

<p><a class="mini link" href={`#/projects/${pid}/channels`}>‹ back to channels</a></p>

{#if error}<p class="error">{error}</p>{/if}

{#if !data}
  <p class="muted">Loading…</p>
{:else}
  <header class="ch-head">
    <h2>{data.channel.title || data.channel.channel_id}</h2>
    <p class="muted">
      {#if data.channel.handle}{data.channel.handle} · {/if}
      {compactNumber(data.channel.subscribers)} subscribers ·
      {compactNumber(data.channel.total_views)} total views ·
      {data.channel.video_count ?? '—'} videos
    </p>
  </header>

  {#if collecting}
    <p class="collecting">Collecting data — provisional scores from tomorrow.</p>
  {/if}

  <section class="tiles">
    <article class="card tile">
      <h3>Baselines</h3>
      <p class="muted">Median views per format (rolling window).</p>
      <div class="tile-row">
        <span>Long-form</span>
        <b class="mono">
          {#if data.baselines.long}
            {compactNumber(data.baselines.long.median)} <span class="muted">(n={data.baselines.long.sample})</span>
          {:else}insufficient data{/if}
        </b>
      </div>
      <div class="tile-row">
        <span>Shorts</span>
        <b class="mono">
          {#if data.baselines.short}
            {compactNumber(data.baselines.short.median)} <span class="muted">(n={data.baselines.short.sample})</span>
          {:else}insufficient data{/if}
        </b>
      </div>
    </article>

    <article class="card tile">
      <h3>Engagement</h3>
      <p class="muted">Median per format, across the channel's videos.</p>
      <div class="tile-row">
        <span>Like rate</span>
        <b class="mono">
          {pct(data.engagement.long.like_rate_median)}
          <span class="muted">long</span> ·
          {pct(data.engagement.short.like_rate_median)}
          <span class="muted">short</span>
        </b>
      </div>
      <div class="tile-row">
        <span>Comment rate</span>
        <b class="mono">
          {pct(data.engagement.long.comment_rate_median)}
          <span class="muted">long</span> ·
          {pct(data.engagement.short.comment_rate_median)}
          <span class="muted">short</span>
        </b>
      </div>
    </article>
  </section>

  <section>
    <h2>Cadence</h2>
    <p class="muted">{data.cadence.uploads_per_week.toFixed(1)} uploads/week</p>
    <h3>By weekday</h3>
    <div class="spark bars">
      {#each weekdayBars as b (b.label)}
        <div class="bar-col" title={`${b.label}: ${b.count} uploads`}>
          <div class="bar-well">
            <div class="spark-bar" style="height:{Math.round((b.count / maxWeekday) * 100)}%"></div>
          </div>
          <span class="bar-label">{b.label}</span>
        </div>
      {/each}
    </div>
    <h3>By hour (UTC)</h3>
    <div class="spark bars">
      {#each hourBars as b (b.key)}
        <div class="bar-col" title={b.title}>
          <div class="bar-well">
            <div class="spark-bar" style="height:{Math.round((b.count / maxHour) * 100)}%"></div>
          </div>
          <span class="bar-label">{b.label}</span>
        </div>
      {/each}
    </div>
  </section>

  <section>
    <h2>Outliers</h2>
    <FormatTabs
      bind:value={outlierFmt}
      counts={{ long: data.outliers.long.length, short: data.outliers.short.length }}
    />
    <OutlierTable rows={data.outliers[outlierFmt]} {pid} {cid} />
  </section>

  <section>
    <h2>Packaging changes</h2>
    {#if data.packaging.length === 0}
      <p class="muted">No packaging change detected yet.</p>
    {:else}
      <ul class="list">
        {#each data.packaging as p (p.video_id + p.field + p.detected_at)}
          {@const v = verdict(p)}
          <li class="row">
            <div class="grow">
              <a href={videoHref(p)}>{p.title || p.video_id}</a>
              <div class="muted">{p.field} changed {shortDate(p.detected_at)}</div>
            </div>
            <span class="mono muted">{vphLabel(p.vph_before)} → {vphLabel(p.vph_after)}</span>
            <span class="badge {v.cls}">{v.label}</span>
          </li>
        {/each}
      </ul>
    {/if}
  </section>

  <section>
    <h2>Resurgence alerts</h2>
    <FormatTabs
      bind:value={resurgenceFmt}
      counts={{ long: data.resurgences.long.length, short: data.resurgences.short.length }}
    />
    {#if data.resurgences[resurgenceFmt].length === 0}
      <p class="muted">No resurgence detected.</p>
    {:else}
      <ul class="list">
        {#each data.resurgences[resurgenceFmt] as r (r.video_id)}
          <li class="row">
            <div class="grow"><a href={videoHref(r)}>{r.title || r.video_id}</a></div>
            <span class="mono">{multiplier(r.ratio)} its recent average</span>
            <span class="mono muted">{vphLabel(r.current_vph)} vs {vphLabel(r.recent_avg_vph)}</span>
          </li>
        {/each}
      </ul>
    {/if}
  </section>
{/if}
