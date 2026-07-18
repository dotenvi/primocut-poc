<script>
  import { onMount, onDestroy } from 'svelte'
  import { api, DAILY_QUOTA } from '../lib/api.js'
  import { href, goto } from '../lib/router.js'
  import { compactNumber } from '../lib/format.js'

  export let route // parsed route object from App

  let projects = []
  let alerts = 0
  let quota = null
  let expanded = {} // pid → open? (a plain object so the {#if} reacts to it)
  let creating = false
  let newName = ''
  let busy = false

  async function loadProjects() {
    try {
      projects = await api.projects()
    } catch (_) {
      projects = []
    }
  }

  async function loadGlobals() {
    try { alerts = (await api.alerts()).length } catch (_) { alerts = 0 }
    try { quota = (await api.activity({ limit: 1 })).quota_units } catch (_) { quota = null }
  }

  async function refresh() {
    await Promise.all([loadProjects(), loadGlobals()])
  }

  const onRefresh = () => refresh()
  onMount(() => {
    refresh()
    window.addEventListener('primocut:refresh', onRefresh)
  })
  onDestroy(() => window.removeEventListener('primocut:refresh', onRefresh))

  // Auto-open the active project's branch when you *navigate* into it — but only
  // once per pid change, so a manual collapse via the chevron isn't snapped back
  // open (this reactive block also reads `expanded`, and would otherwise re-fire
  // on every toggle and force the active branch back open).
  let autoOpenedPid = null
  $: if (route.pid && route.pid !== autoOpenedPid) {
    autoOpenedPid = route.pid
    expanded = { ...expanded, [route.pid]: true }
  }

  function toggle(pid) {
    expanded = { ...expanded, [pid]: !expanded[pid] }
  }

  async function create() {
    if (!newName.trim() || busy) return
    busy = true
    try {
      const p = await api.createProject({ name: newName.trim() })
      newName = ''
      creating = false
      await loadProjects()
      goto(href.lives(p.id))
    } catch (_) {
      /* keep the form open on error */
    } finally {
      busy = false
    }
  }

  const livesActive = (pid) =>
    String(route.pid) === String(pid) && (route.view === 'lives' || route.view === 'cutroom')
  const nicheActive = (pid) =>
    String(route.pid) === String(pid) &&
    (route.view === 'niche' || route.view === 'video' || route.view === 'channel')
  const settingsActive = (pid) => String(route.pid) === String(pid) && route.view === 'settings'
</script>

<aside class="sticky top-0 h-screen border-r border-line bg-panel px-2.5 py-4 flex flex-col max-[960px]:hidden">
  <a href={href.radar()} class="flex items-center gap-2.5 px-2 pb-4 no-underline">
    <span class="w-5 h-5 rounded border-2 border-accent relative after:content-[''] after:absolute after:inset-[3px] after:bg-accent after:rounded-[1px]"></span>
    <span class="font-semibold tracking-[0.2em] text-[13px] text-ink">PRIMOCUT</span>
  </a>

  <div class="flex-1 min-h-0 overflow-y-auto flex flex-col">
  <a href={href.radar()} class="nav no-underline" class:on={route.view === 'radar'}>
    <span class="w-3.5 text-[11px] text-accent">◈</span>Radar
    {#if alerts > 0}
      <span class="ml-auto font-mono text-[10px] bg-heat-bg99 text-heat-99 rounded-full px-1.5 border border-heat-ln99">{alerts}</span>
    {/if}
  </a>
  <a href={href.activity()} class="nav no-underline" class:on={route.view === 'activity'}>
    <span class="w-3.5 text-[11px] text-ink-3">≡</span>Activity
  </a>

  <div class="pt-4 pb-1 mt-2.5 px-2 border-t border-line"><span class="eyebrow">Projects</span></div>

  {#each projects as p (p.id)}
    <div
      class="flex items-center gap-2 px-2 py-1.5 rounded-md text-[13px] hover:bg-card"
      class:text-ink-2={String(route.pid) !== String(p.id)}
    >
      <button
        class="font-mono text-[8px] text-ink-3 w-2 bg-transparent border-0 cursor-pointer p-0"
        on:click|stopPropagation={() => toggle(p.id)}
        aria-label="Toggle {p.name}"
      >{expanded[p.id] ? '▼' : '▶'}</button>
      <a href={href.lives(p.id)} class="font-medium truncate no-underline flex-1 text-inherit">{p.name}</a>
    </div>
    {#if expanded[p.id]}
      <div class="pl-3.5 ml-3 border-l border-line">
        <a href={href.lives(p.id)} class="nav no-underline" class:on={livesActive(p.id)}>
          <span class="w-3.5 text-[11px] text-ink-3">▷</span>Cut Room
          <span class="ml-auto font-mono text-[10.5px] text-ink-3">{p.live_count ?? 0}</span>
        </a>
        <a href={href.niche(p.id)} class="nav no-underline" class:on={nicheActive(p.id)}>
          <span class="w-3.5 text-[11px] text-ink-3">▤</span>Niche
          <span class="ml-auto font-mono text-[10.5px] text-ink-3">{p.channel_count ?? 0}</span>
        </a>
        <a href={href.settings(p.id)} class="nav no-underline" class:on={settingsActive(p.id)}>
          <span class="w-3.5 text-[11px] text-ink-3">⚙</span>Settings
        </a>
      </div>
    {/if}
  {/each}

  {#if creating}
    <div class="px-2 py-1.5 mt-1">
      <input
        type="text"
        bind:value={newName}
        placeholder="Project name"
        on:keydown={(e) => { if (e.key === 'Enter') create(); if (e.key === 'Escape') creating = false }}
      />
    </div>
  {:else}
    <button
      class="flex items-center gap-2 px-2 py-1.5 mt-2 text-[13px] text-ink-3 cursor-pointer hover:bg-card rounded-md bg-transparent border-0 text-left"
      on:click={() => (creating = true)}
    >
      <span class="font-mono text-[8px] w-2">+</span>New project
    </button>
  {/if}
  </div>

  <div class="pt-3 px-2 border-t border-line">
    <div class="font-mono text-[10px] flex justify-between text-ink-3">
      <span>YouTube API quota</span>
      <span class="text-ok">{quota == null ? '—' : compactNumber(quota)} / {DAILY_QUOTA.toLocaleString()}</span>
    </div>
  </div>
</aside>
