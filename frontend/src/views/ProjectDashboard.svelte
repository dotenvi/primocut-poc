<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import OutlierTable from '../components/OutlierTable.svelte'
  import FormatTabs from '../components/FormatTabs.svelte'
  import { pct, vphLabel, multiplier, shortDate } from '../lib/format.js'

  export let pid

  let data = null // null while loading
  let error = ''
  let notice = ''
  let outlierFmt = 'long'
  let patternFmt = 'long'
  let resurgenceFmt = 'long'

  async function load() {
    try {
      data = await api.projectDashboard(pid)
      error = ''
    } catch (e) {
      error = e.message
    }
  }
  onMount(load)

  // Opt-in batch: enriches the definitive outliers only (the same set the
  // patterns below are computed from). Costs one LLM call per un-cached video.
  async function enrich(kind) {
    notice = ''
    try {
      const r = await api.enrichProject(pid, kind)
      const verb = kind === 'comments' ? 'Synthesising the comments of' : `Enriching the ${kind} of`
      notice = r.videos
        ? `${verb} ${r.videos} outlier${r.videos === 1 ? '' : 's'} — ` +
          'open a video to follow it. Videos analysed before are not charged again.'
        : 'No definitive outlier to enrich yet.'
    } catch (e) {
      error = e.message
    }
  }

  // Channels are linked but nothing is scored yet — the snapshots have not
  // accumulated. Say so instead of rendering an empty niche view. Neither
  // format having an outlier is what "nothing scored yet" means.
  $: collecting =
    data &&
    data.channels.length > 0 &&
    data.outliers.long.length === 0 &&
    data.outliers.short.length === 0

  const REQUEST_LABEL = {
    guest: 'Guests to book',
    topic: 'Subjects to cover',
    format: 'Format asks',
    other: 'Other asks',
  }
  // Only the types the audience actually asked for get a column.
  $: requestTypes = data
    ? Object.entries(data.audience_requests.by_type).filter(([, rows]) => rows.length > 0)
    : []

  const videoHref = (r) => `#/projects/${pid}/channels/${r.channel_id}/videos/${r.video_id}`

  // The packaging verdict is derived from the sign of the impact delta; a change
  // whose 7-day window has not elapsed yet has no before/after to judge.
  function verdict(p) {
    if (p.delta == null) return { label: 'impact window not elapsed', cls: '' }
    if (p.delta > 0) return { label: `▲ +${vphLabel(p.delta)}`, cls: 'st-done' }
    if (p.delta < 0) return { label: `▼ ${vphLabel(p.delta)}`, cls: 'st-failed' }
    return { label: 'no change', cls: '' }
  }
</script>

{#if error}<p class="error">{error}</p>{/if}

{#if !data}
  <p class="muted">Loading…</p>
{:else if data.channels.length === 0}
  <p class="muted">
    No channels in this project yet —
    <a href={`#/projects/${pid}/channels`}>add a competitor</a> to see the niche view.
  </p>
{:else}
  {#if collecting}
    <p class="collecting">Collecting data — provisional scores from tomorrow.</p>
  {/if}

  <section>
    <h2>Outliers (niche)</h2>
    <FormatTabs
      bind:value={outlierFmt}
      counts={{ long: data.outliers.long.length, short: data.outliers.short.length }}
    />
    <OutlierTable rows={data.outliers[outlierFmt]} {pid} showChannel={true} />
  </section>

  <section>
    <div class="tab-bar flush">
      <h2 class="grow">Patterns</h2>
      <button class="mini ai tab-bar-action" on:click={() => enrich('thumbnail')}>
        ✨ Enrich outlier thumbnails
      </button>
      <button class="mini ai" on:click={() => enrich('title')}>
        ✨ Enrich outlier titles
      </button>
    </div>
    {#if notice}<p class="collecting">{notice}</p>{/if}
    <FormatTabs
      bind:value={patternFmt}
      counts={{ long: data.patterns.long.length, short: data.patterns.short.length }}
    />
    {#if data.patterns[patternFmt].length === 0}
      <p class="muted">Not enough definitive outliers yet to report patterns.</p>
    {:else}
      <p class="muted">
        Packaging attributes among definitive outliers vs. the same-format population. Each source
        is measured against the videos analysed for it, so AI patterns appear only once enough
        outliers have been enriched.
      </p>
      <div class="otable-wrap">
        <table class="otable">
          <thead>
            <tr>
              <th>Attribute</th>
              <th>Source</th>
              <th class="num">Outliers</th>
              <th class="num">Population</th>
              <th class="num">Lift</th>
              <th class="num">n</th>
            </tr>
          </thead>
          <tbody>
            {#each data.patterns[patternFmt] as p (p.source + p.attribute)}
              <tr>
                <td>{p.attribute}</td>
                <td><span class="tag">{p.source}</span></td>
                <td class="num mono">{pct(p.outlier_rate)}</td>
                <td class="num mono">{pct(p.population_rate)}</td>
                <td class="num mono">{multiplier(p.lift)}</td>
                <td class="num mono muted">{p.outlier_occurrences}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}
  </section>

  <section>
    <div class="tab-bar flush">
      <h2 class="grow">Audience requests</h2>
      <button class="mini ai tab-bar-action" on:click={() => enrich('comments')}>
        ✨ Synthesise outlier comments
      </button>
    </div>
    {#if requestTypes.length === 0}
      <p class="muted">
        No comments synthesised yet. Comments are collected automatically for the outliers above;
        reading them costs an LLM call, so it only runs when you ask.
      </p>
    {:else}
      <p class="muted">
        What the audience explicitly asked for across {data.audience_requests.videos_synthesised}
        synthesised {data.audience_requests.videos_synthesised === 1 ? 'video' : 'videos'}, ranked
        by how many videos asked.
      </p>
      <div class="req-cols">
        {#each requestTypes as [type, rows] (type)}
          <article class="card">
            <h3>{REQUEST_LABEL[type]}</h3>
            <ul class="list">
              {#each rows as r (r.value)}
                <li class="row">
                  <span class="grow">{r.value}</span>
                  <span class="mono muted">
                    {r.videos} {r.videos === 1 ? 'video' : 'videos'} · {r.mentions}×
                  </span>
                </li>
              {/each}
            </ul>
          </article>
        {/each}
      </div>
    {/if}
  </section>

  <section>
    <h2>Packaging log</h2>
    {#if data.packaging.length === 0}
      <p class="muted">No packaging change detected yet.</p>
    {:else}
      <ul class="list">
        {#each data.packaging as p (p.video_id + p.field + p.detected_at)}
          {@const v = verdict(p)}
          <li class="row">
            <div class="grow">
              <a href={videoHref(p)}>{p.title || p.video_id}</a>
              <div class="muted">
                {p.channel_title} · {p.field} changed {shortDate(p.detected_at)}
              </div>
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
            <div class="grow">
              <a href={videoHref(r)}>{r.title || r.video_id}</a>
            </div>
            <span class="mono">{multiplier(r.ratio)} its recent average</span>
            <span class="mono muted">{vphLabel(r.current_vph)}</span>
          </li>
        {/each}
      </ul>
    {/if}
  </section>
{/if}
