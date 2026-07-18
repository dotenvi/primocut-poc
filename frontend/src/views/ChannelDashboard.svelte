<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { href, goto } from '../lib/router.js'
  import { compactNumber, pct, vphLabel, multiplier, WEEKDAYS } from '../lib/format.js'

  export let pid
  export let cid

  let data = null
  let error = ''
  let fmt = 'long'

  async function load() {
    try { data = await api.channelDashboard(cid); error = '' } catch (e) { error = e.message }
  }
  onMount(load)

  $: weekdayBars = data
    ? WEEKDAYS.map((label, i) => ({ label, count: data.cadence.by_weekday[String(i)] || 0 }))
    : []
  $: maxWeekday = Math.max(1, ...weekdayBars.map((b) => b.count))
  $: outliers = data ? data.outliers[fmt] : []
</script>

<a href={href.niche(pid, 'channels')} class="eyebrow cursor-pointer hover:text-ink-2 mb-2 block no-underline">‹ Niche / Channels</a>

{#if error}<p class="m text-err mb-4">{error}</p>{/if}

{#if !data}
  <p class="m">Loading…</p>
{:else}
  <div class="mb-5">
    <h1>{data.channel.title || data.channel.channel_id}</h1>
    <p class="m mt-1">
      {#if data.channel.handle}{data.channel.handle} · {/if}
      {compactNumber(data.channel.subscribers)} subscribers · {compactNumber(data.channel.total_views)} total views · {data.channel.video_count ?? '—'} videos
    </p>
  </div>

  <div class="grid grid-cols-2 gap-4 mb-6 max-[960px]:grid-cols-1">
    <div class="card">
      <div class="lbl mb-2">Baselines · median views</div>
      <div class="kv"><span>Long-form</span><span class="font-mono text-[12px]">{data.baselines.long ? `${compactNumber(data.baselines.long.median)} · n=${data.baselines.long.sample}` : 'insufficient'}</span></div>
      <div class="kv"><span>Shorts</span><span class="font-mono text-[12px]">{data.baselines.short ? `${compactNumber(data.baselines.short.median)} · n=${data.baselines.short.sample}` : 'insufficient'}</span></div>
    </div>
    <div class="card">
      <div class="lbl mb-2">Engagement · median</div>
      <div class="kv"><span>Like rate</span><span class="font-mono text-[12px]">{pct(data.engagement.long.like_rate_median)} long · {pct(data.engagement.short.like_rate_median)} short</span></div>
      <div class="kv"><span>Comment rate</span><span class="font-mono text-[12px]">{pct(data.engagement.long.comment_rate_median)} long · {pct(data.engagement.short.comment_rate_median)} short</span></div>
    </div>
  </div>

  <div class="lbl mb-2">Cadence · {data.cadence.uploads_per_week.toFixed(1)} uploads/week</div>
  <div class="flex items-end gap-1 h-20 mb-6">
    {#each weekdayBars as b (b.label)}
      <div class="flex-1 flex flex-col items-center gap-1.5" title="{b.label}: {b.count}">
        <div class="w-full flex items-end h-16"><div class="w-full bg-[#3f4145] rounded-t-[2px]" style="height:{Math.round((b.count / maxWeekday) * 100)}%"></div></div>
        <span class="font-mono text-[9px] text-ink-3">{b.label}</span>
      </div>
    {/each}
  </div>

  <div class="flex items-center gap-3 mb-3">
    <span class="lbl">Outliers</span>
    <div class="flex gap-1.5">
      <button class="chip" class:on={fmt === 'long'} on:click={() => (fmt = 'long')}>Long {data.outliers.long.length}</button>
      <button class="chip" class:on={fmt === 'short'} on:click={() => (fmt = 'short')}>Shorts {data.outliers.short.length}</button>
    </div>
  </div>
  {#if outliers.length === 0}
    <p class="m">No outlier in this format yet.</p>
  {:else}
    <table class="w-full border-collapse text-[13px]">
      <thead><tr><th class="w-7 text-right">#</th><th>Video</th><th class="text-right">Band</th><th class="text-right">× median</th><th class="text-right">Views</th></tr></thead>
      <tbody>
        {#each outliers as o, i (o.video_id)}
          <tr
            class="hover:bg-[#15181C] cursor-pointer"
            tabindex="0"
            on:click={() => goto(href.video(pid, cid, o.video_id))}
            on:keydown={(e) => e.key === 'Enter' && goto(href.video(pid, cid, o.video_id))}
          >
            <td class="text-right font-mono text-[12px] text-ink-3">{i + 1}</td>
            <td>{o.title || o.video_id}{#if o.provisional}<span class="m text-accent"> · provisional</span>{/if}</td>
            <td class="text-right">{#if o.band}<span class="tag bg-heat-bg99 text-heat-99 border-heat-ln99">{o.percentile ? `p${o.percentile}` : o.band}</span>{/if}</td>
            <td class="text-right font-mono text-[12px]">{multiplier(o.median_multiple)}</td>
            <td class="text-right font-mono text-[12px]">{compactNumber(o.views)}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}
{/if}
