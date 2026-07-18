<script>
  import { onMount, onDestroy } from 'svelte'
  import { api, backfillActive } from '../lib/api.js'
  import { href, goto } from '../lib/router.js'
  import { multiplier, pctCi, compactNumber, vphLabel, shortDate } from '../lib/format.js'

  export let pid
  export let tab = 'patterns'

  let project = null
  let dash = null
  let channels = null
  let error = ''
  let notice = ''
  let fmt = 'long'
  let url = ''
  let busy = false
  let timer

  async function loadDash() {
    try { dash = await api.projectDashboard(pid); error = '' } catch (e) { error = e.message }
  }
  async function loadChannels() {
    try { channels = await api.channels(pid) } catch (e) { error = e.message }
  }
  async function loadProject() {
    try { project = await api.project(pid) } catch (_) { project = null }
  }

  function channelsPending() {
    return !channels || channels.some((c) => backfillActive(c.backfill.state))
  }
  onMount(() => {
    loadProject()
    loadDash()
    loadChannels()
    timer = setInterval(() => { if (channelsPending()) loadChannels() }, 2000)
  })
  onDestroy(() => clearInterval(timer))

  const go = (t) => goto(href.niche(pid, t))

  async function enrich(kind) {
    notice = ''
    try {
      const r = await api.enrichProject(pid, kind)
      notice = r.videos
        ? `Enriching the ${kind} of ${r.videos} outlier${r.videos === 1 ? '' : 's'} — open a video to follow it.`
        : 'No definitive outlier to enrich yet.'
    } catch (e) { error = e.message }
  }

  async function addChannel() {
    if (!url || busy) return
    busy = true; error = ''; notice = ''
    try {
      const r = await api.addChannel(pid, url)
      notice = r.existing ? `Linked “${r.title}” — already tracked.` : `Added “${r.title}” — backfill queued.`
      url = ''
      await loadChannels()
      window.dispatchEvent(new Event('primocut:refresh'))
    } catch (e) { error = e.message } finally { busy = false }
  }

  async function detach(c) {
    if (!confirm(`Detach “${c.title}” from this niche? All collected data is kept.`)) return
    try {
      await api.detachChannel(pid, c.channel_id)
      await loadChannels()
      window.dispatchEvent(new Event('primocut:refresh'))
    } catch (e) { error = e.message }
  }

  const heat = (lift) => (lift >= 1.05 ? 'text-heat-99' : 'text-ink-3')
  $: patterns = dash ? dash.patterns[fmt] : []
  $: outliers = dash ? dash.outliers[fmt] : []
</script>

<div class="flex items-end justify-between gap-3 mb-5">
  <div><div class="eyebrow">{project?.name || '…'}</div><h1>Niche</h1></div>
  {#if tab === 'patterns'}
    <div class="flex gap-1.5">
      <button class="btn btn-ai" on:click={() => enrich('thumbnail')}>Enrich thumbnails</button>
      <button class="btn btn-ai" on:click={() => enrich('title')}>Enrich titles</button>
    </div>
  {/if}
</div>

<div class="flex border-b border-line mb-5">
  <button class="tab" class:on={tab === 'patterns'} on:click={() => go('patterns')}>Patterns</button>
  <button class="tab" class:on={tab === 'outliers'} on:click={() => go('outliers')}>Outliers</button>
  <button class="tab" class:on={tab === 'channels'} on:click={() => go('channels')}>Channels</button>
</div>

{#if error}<p class="m text-err mb-4">{error}</p>{/if}
{#if notice}<p class="m text-accent mb-4">{notice}</p>{/if}

{#if tab === 'channels'}
  {#if channels === null}
    <p class="m">Loading…</p>
  {:else}
    <table class="w-full border-collapse text-[13px]">
      <thead><tr><th>Competitor</th><th>Handle</th><th class="text-right">Backfill</th><th></th></tr></thead>
      <tbody>
        {#each channels as c (c.channel_id)}
          <tr class="hover:bg-[#15181C]">
            <td>
              <a href={href.channel(pid, c.channel_id)} class="no-underline hover:text-accent">{c.title || c.channel_id}</a>
            </td>
            <td class="text-ink-2">{c.handle || '—'}</td>
            <td class="text-right">
              {#if c.backfill.state === 'done'}
                <span class="m">{c.backfill.total} videos</span>
              {:else if c.backfill.state === 'error'}
                <span class="tag bg-err-bg text-err border-err-line">failed</span>
              {:else if backfillActive(c.backfill.state)}
                <span class="m text-accent">{c.backfill.done}/{c.backfill.total || '?'}</span>
              {:else}
                <span class="m">{c.backfill.state}</span>
              {/if}
            </td>
            <td class="text-right"><button class="btn btn-xs" on:click={() => detach(c)}>Detach</button></td>
          </tr>
        {/each}
      </tbody>
    </table>
    {#if channels.length === 0}<p class="m mt-3">No competitor tracked yet.</p>{/if}
    <div class="mt-4 flex gap-2.5 items-center">
      <input type="text" class="max-w-[320px]" placeholder="youtube.com/@channel" bind:value={url}
        on:keydown={(e) => e.key === 'Enter' && addChannel()} />
      <button class="btn shrink-0" on:click={addChannel} disabled={busy || !url}>Track channel</button>
    </div>
  {/if}
{:else if !dash}
  <p class="m">Loading…</p>
{:else}
  <div class="flex gap-1.5 mb-4">
    <button class="chip" class:on={fmt === 'long'} on:click={() => (fmt = 'long')}>Long-form {dash[tab === 'outliers' ? 'outliers' : 'patterns'].long.length}</button>
    <button class="chip" class:on={fmt === 'short'} on:click={() => (fmt = 'short')}>Shorts {dash[tab === 'outliers' ? 'outliers' : 'patterns'].short.length}</button>
  </div>

  {#if tab === 'patterns'}
    <p class="text-ink-2 mb-4 max-w-[640px]">Attributes among definitive outliers (p90 and above) versus the same-format control, reported only when their 95% Wilson intervals separate.</p>
    {#if patterns.length === 0}
      <p class="m">No attribute separates outliers from the control group yet.</p>
    {:else}
      <table class="w-full border-collapse text-[13px]">
        <thead><tr><th>Attribute</th><th>Source</th><th class="text-right">Outliers</th><th class="text-right">Control</th><th class="text-right">Lift</th><th class="text-right">n</th></tr></thead>
        <tbody>
          {#each patterns as p (p.source + p.attribute)}
            <tr class="hover:bg-[#15181C]">
              <td class="font-mono text-[12px]">{p.attribute}</td>
              <td><span class="tag bg-card2 text-ink-2 border-line-strong">{p.source}</span></td>
              <td class="text-right font-mono text-[12px]">{pctCi(p.freq_outlier, p.ci_outlier)}</td>
              <td class="text-right font-mono text-[12px]">{pctCi(p.freq_control, p.ci_control)}</td>
              <td class="text-right font-mono text-[12px] {heat(p.lift)}">{multiplier(p.lift)}</td>
              <td class="text-right font-mono text-[12px] text-ink-3">{p.n_outlier}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  {:else}
    {#if outliers.length === 0}
      <p class="m">No outlier in this format yet.</p>
    {:else}
      <table class="w-full border-collapse text-[13px]">
        <thead><tr><th class="w-7 text-right">#</th><th>Video</th><th>Channel</th><th class="text-right">Band</th><th class="text-right">× median</th><th class="text-right">VPH</th><th class="text-right">Views</th></tr></thead>
        <tbody>
          {#each outliers as o, i (o.video_id)}
            <tr
              class="hover:bg-[#15181C] cursor-pointer"
              tabindex="0"
              on:click={() => goto(href.video(pid, o.channel_id, o.video_id))}
              on:keydown={(e) => e.key === 'Enter' && goto(href.video(pid, o.channel_id, o.video_id))}
            >
              <td class="text-right font-mono text-[12px] text-ink-3">{i + 1}</td>
              <td>{o.title || o.video_id}{#if o.provisional}<span class="m text-accent"> · provisional</span>{/if}</td>
              <td class="text-ink-2">{o.channel_title || o.channel_id}</td>
              <td class="text-right">{#if o.band}<span class="tag bg-heat-bg99 text-heat-99 border-heat-ln99">{o.percentile ? `p${o.percentile}` : o.band}</span>{/if}</td>
              <td class="text-right font-mono text-[12px]">{multiplier(o.median_multiple)}</td>
              <td class="text-right font-mono text-[12px]">{vphLabel(o.current_vph)}</td>
              <td class="text-right font-mono text-[12px]">{compactNumber(o.views)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    {/if}
  {/if}
{/if}
