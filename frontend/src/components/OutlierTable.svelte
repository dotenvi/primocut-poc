<script>
  import { compactNumber, vphLabel, multiplier } from '../lib/format.js'

  export let rows = []
  export let pid // project id — needed to build video-detail links
  export let cid = '' // fixed channel id (channel dashboard); ignored when showChannel
  export let showChannel = false // niche/project variant: each row carries channel_id + current_vph

  const rowCid = (r) => (showChannel ? r.channel_id : cid)
  const videoHref = (r) => `#/projects/${pid}/channels/${rowCid(r)}/videos/${r.video_id}`
</script>

{#if rows.length === 0}
  <p class="muted">No outliers yet.</p>
{:else}
  <div class="otable-wrap">
    <table class="otable">
      <thead>
        <tr>
          <th class="num">#</th>
          <th>Video</th>
          {#if showChannel}<th>Channel</th>{/if}
          <th class="num">Score</th>
          <th>Band</th>
          {#if showChannel}<th class="num">VPH</th>{/if}
          <th class="num">Views</th>
        </tr>
      </thead>
      <tbody>
        {#each rows as r, i (r.video_id)}
          <tr>
            <td class="num muted">{i + 1}</td>
            <td>
              <a href={videoHref(r)}>{r.title || r.video_id}</a>
              {#if r.provisional}<span class="status status-provisional">provisional</span>{/if}
            </td>
            {#if showChannel}<td class="muted">{r.channel_title || r.channel_id}</td>{/if}
            <td class="num mono">{multiplier(r.score)}</td>
            <td>{#if r.bracket}<span class="badge st-done">≥{r.bracket}</span>{/if}</td>
            {#if showChannel}<td class="num mono">{vphLabel(r.current_vph)}</td>{/if}
            <td class="num mono">{compactNumber(r.views)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
{/if}
