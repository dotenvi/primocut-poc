<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { href, goto } from '../lib/router.js'
  import { timeAgo } from '../lib/format.js'
  import { eventTag, eventMeta, eventFormat } from '../lib/events.js'
  import DistributionStrip from '../components/DistributionStrip.svelte'

  let projects = []
  let alerts = []
  let events = []
  let dists = {} // `${channel_id}:${fmt}` → distribution payload
  let filter = 'all' // 'all' | project id
  let error = ''
  let loaded = false

  // "Nothing new since <last visit>" — the moment the user last cleared the feed.
  let lastVisit = localStorage.getItem('primocut:lastSeen') || ''

  async function loadDistributions() {
    // One batched call per format present among the visible cards' channels — no
    // per-card request, no N+1. Only cards that carry a channel + view count.
    const byFmt = { long: new Set(), short: new Set() }
    for (const ev of events) {
      if (ev.channel_id && (ev.payload || {}).views != null) byFmt[eventFormat(ev)].add(ev.channel_id)
    }
    const next = {}
    for (const fmt of ['long', 'short']) {
      const ids = [...byFmt[fmt]]
      if (!ids.length) continue
      try {
        const map = await api.distributionBatch(ids, fmt)
        for (const [cid, d] of Object.entries(map)) next[`${cid}:${fmt}`] = d
      } catch (_) {
        /* the strip is non-essential — a failure just hides it */
      }
    }
    dists = next
  }

  async function load() {
    try {
      const params = filter === 'all' ? {} : { project_id: filter }
      ;[projects, alerts, events] = await Promise.all([
        api.projects(),
        api.alerts(),
        api.events({ ...params, limit: 100 }),
      ])
      error = ''
      await loadDistributions()
    } catch (e) {
      error = e.message
    } finally {
      loaded = true
    }
  }
  onMount(load)

  async function setFilter(f) {
    filter = f
    await load()
  }

  async function markAllSeen() {
    const now = new Date().toISOString()
    try {
      await api.markSeen({ until: now })
      localStorage.setItem('primocut:lastSeen', now)
      lastVisit = now
      window.dispatchEvent(new Event('primocut:refresh'))
      await load()
    } catch (e) {
      error = e.message
    }
  }

  async function requeue(ev) {
    if (!ev.project_id || !ev.live_id) return
    try {
      await api.reenqueue(ev.project_id, ev.live_id)
      alerts = alerts.filter((a) => a.id !== ev.id) // optimistic — it re-runs now
    } catch (e) {
      error = e.message
    }
  }

  const dismiss = (ev) => (alerts = alerts.filter((a) => a.id !== ev.id))

  // A card links to the video within the (first) project that tracks its channel.
  function openEvent(ev) {
    const pid = filter !== 'all' ? filter : ev.projects?.[0]?.id
    if (pid && ev.channel_id && ev.video_id) goto(href.video(pid, ev.channel_id, ev.video_id))
  }

  const distFor = (ev) => dists[`${ev.channel_id}:${eventFormat(ev)}`]
  $: unseen = events.filter((e) => !e.seen_at)
  $: seen = events.filter((e) => e.seen_at)
  const key = (ev) => (ev.projects?.[0]?.name || ev.channel_title || '')
</script>

<div class="flex items-end justify-between gap-3 mb-5">
  <div><div class="eyebrow">All niches</div><h1>Radar</h1></div>
  {#if unseen.length}
    <button class="btn" on:click={markAllSeen}>Mark all as seen</button>
  {/if}
</div>

{#if error}<p class="m text-err mb-4">{error}</p>{/if}

{#if alerts.length}
  <div class="border border-err-line bg-err-bg rounded-card px-3.5 py-2.5 mb-6">
    <div class="flex items-center justify-between mb-1">
      <span class="lbl text-err">Needs you · {alerts.length}</span>
      <a href={href.activity()} class="m text-[10px] cursor-pointer hover:text-ink no-underline">View all activity →</a>
    </div>
    {#each alerts as a (a.id)}
      <div class="flex items-center gap-2.5 py-1.5 border-t border-[#331B1D] first:border-t-0">
        <span class="w-1.5 h-1.5 rounded-full bg-err shrink-0"></span>
        <span class="flex-1">{a.title || a.payload?.message || a.type.replace(/_/g, ' ')}</span>
        <span class="m">{a.channel_title || a.payload?.detail || ''}</span>
        {#if a.live_id && a.project_id}
          <button class="btn btn-xs" on:click={() => requeue(a)}>Requeue</button>
        {/if}
        <button class="btn btn-xs" on:click={() => dismiss(a)}>Dismiss</button>
      </div>
    {/each}
  </div>
{/if}

<div class="flex gap-1.5 flex-wrap mb-4">
  <button class="chip" class:on={filter === 'all'} on:click={() => setFilter('all')}>All</button>
  {#each projects as p (p.id)}
    <button class="chip" class:on={String(filter) === String(p.id)} on:click={() => setFilter(p.id)}>{p.name}</button>
  {/each}
</div>

{#if !loaded}
  <p class="m">Loading…</p>
{:else if unseen.length === 0 && seen.length === 0}
  <div class="border border-line rounded-card py-10 text-center bg-panel">
    <p class="text-ink-2">Nothing new{lastVisit ? ` since ${new Date(lastVisit).toLocaleString()}` : ''}.</p>
    <p class="m mt-2">
      {#if projects.length}
        Radar fills as the snapshot cycle detects outliers. Open a project to see its lives and niche.
      {:else}
        Create a project in the sidebar to start tracking a niche.
      {/if}
    </p>
  </div>
{:else}
  {#if unseen.length}
    <div class="lbl mb-2">New</div>
    {#each unseen as ev (ev.id)}
      {@const tag = eventTag(ev)}
      {@const dist = distFor(ev)}
      <div
        class="ev"
        role="button"
        tabindex="0"
        on:click={() => openEvent(ev)}
        on:keydown={(e) => e.key === 'Enter' && openEvent(ev)}
      >
        <div class="w-[78px] h-11 rounded-[3px] bg-sunk border border-line shrink-0 grid place-items-center font-mono text-[8px] text-ink-3">thumb</div>
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2 flex-wrap">
            <span class="tag {tag.cls}">{tag.text}</span>
            {#each ev.projects || [] as pr (pr.id)}
              <span class="tag bg-card2 text-ink-2 border-line-strong">{pr.name}</span>
            {/each}
            <span class="m">{ev.channel_title || ''} · {timeAgo(ev.occurred_at)}</span>
          </div>
          <div class="text-[13px] my-1 leading-snug truncate">{ev.title || ev.video_id || ev.type}</div>
          <div class="m">{eventMeta(ev)}</div>
        </div>
        {#if dist}
          <DistributionStrip buckets={dist.buckets} edges={dist.bucket_edges} markViews={ev.payload?.views} />
        {/if}
      </div>
    {/each}
  {/if}

  {#if seen.length}
    <div class="lbl mt-6 mb-2">Earlier · seen</div>
    {#each seen as ev (ev.id)}
      {@const tag = eventTag(ev)}
      <div
        class="ev seen"
        role="button"
        tabindex="0"
        on:click={() => openEvent(ev)}
        on:keydown={(e) => e.key === 'Enter' && openEvent(ev)}
      >
        <span class="tag {tag.cls}">{tag.abbr}</span>
        <span class="flex-1 truncate text-ink-2 text-[12.5px]">{ev.title || ev.video_id || ev.type}</span>
        <span class="m shrink-0">{ev.channel_title || key(ev)}</span>
      </div>
    {/each}
  {/if}
{/if}
