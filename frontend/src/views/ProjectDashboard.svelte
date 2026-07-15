<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import OutlierTable from '../components/OutlierTable.svelte'
  import { pct, vphLabel, multiplier, shortDate } from '../lib/format.js'

  export let pid

  let data = null // null while loading
  let error = ''

  async function load() {
    try {
      data = await api.projectDashboard(pid)
      error = ''
    } catch (e) {
      error = e.message
    }
  }
  onMount(load)

  // Channels are linked but nothing is scored yet — the snapshots have not
  // accumulated. Say so instead of rendering an empty niche view.
  $: collecting = data && data.channels.length > 0 && data.outliers.length === 0

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
    <OutlierTable rows={data.outliers} {pid} showChannel={true} />
  </section>

  <section>
    <h2>Patterns</h2>
    {#if data.patterns.length === 0}
      <p class="muted">Not enough definitive outliers yet to report patterns.</p>
    {:else}
      <p class="muted">Title attributes among definitive outliers vs. the whole project population.</p>
      <div class="otable-wrap">
        <table class="otable">
          <thead>
            <tr>
              <th>Attribute</th>
              <th class="num">Outliers</th>
              <th class="num">Population</th>
              <th class="num">Lift</th>
              <th class="num">n</th>
            </tr>
          </thead>
          <tbody>
            {#each data.patterns as p (p.attribute)}
              <tr>
                <td>{p.attribute}</td>
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
    {#if data.resurgences.length === 0}
      <p class="muted">No resurgence detected.</p>
    {:else}
      <ul class="list">
        {#each data.resurgences as r (r.video_id)}
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
