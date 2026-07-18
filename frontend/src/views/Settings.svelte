<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'

  export let pid

  let project = null
  let own = null
  let error = ''
  let url = ''
  let handle = ''
  let role = ''
  let busy = false

  async function loadProject() {
    try { project = await api.project(pid) } catch (e) { error = e.message }
  }
  async function loadOwn() {
    try { own = await api.ownChannels(pid) } catch (e) { error = e.message }
  }
  onMount(() => { loadProject(); loadOwn() })

  async function add() {
    if (!url || busy) return
    busy = true; error = ''
    try {
      await api.addOwnChannel(pid, { channel_url: url, handle, title: '', role: role || null })
      url = ''; handle = ''; role = ''
      await loadOwn()
    } catch (e) { error = e.message } finally { busy = false }
  }

  async function remove(c) {
    if (!confirm(`Remove ${c.handle || c.channel_url} from this niche?`)) return
    try { await api.deleteOwnChannel(pid, c.id); await loadOwn() } catch (e) { error = e.message }
  }

  const BAND_ORDER = [
    ['relevant', 'Relevant', 'text-heat-90'],
    ['strong', 'Strong', 'text-heat-97'],
    ['exceptional', 'Exceptional', 'text-heat-99'],
  ]
  $: bands = project ? project.band_config : null
  const bandValue = (key) => {
    if (!bands) return '—'
    const v = bands.cutoffs[key]
    return bands.basis === 'percentile' ? `p${v}` : `${v}×`
  }
</script>

<div class="mb-5"><div class="eyebrow">{project?.name || '…'}</div><h1>Settings</h1></div>

{#if error}<p class="m text-err mb-4">{error}</p>{/if}

<div class="lbl mb-2">My channels in this niche</div>
<p class="text-ink-2 mb-2.5 max-w-[600px]">Any live in this project can become a cut on any of these. They share the same niche patterns.</p>
<div class="card mb-7 max-w-[600px]">
  {#if own === null}
    <p class="m">Loading…</p>
  {:else}
    {#each own as c (c.id)}
      <div class="kv">
        <span>{c.handle || c.channel_url}</span>
        <span class="flex items-center gap-2">
          <span class="m">{c.role || '—'}</span>
          <button class="btn btn-xs" on:click={() => remove(c)}>Remove</button>
        </span>
      </div>
    {/each}
    {#if own.length === 0}<p class="m">No own channel yet.</p>{/if}
    <div class="flex gap-2 items-center mt-3 flex-wrap">
      <input type="text" class="flex-1 min-w-[200px]" placeholder="youtube.com/@yourchannel" bind:value={url}
        on:keydown={(e) => e.key === 'Enter' && add()} />
      <input type="text" class="w-28" placeholder="@handle" bind:value={handle} />
      <input type="text" class="w-24" placeholder="role" bind:value={role} />
      <button class="btn shrink-0" on:click={add} disabled={busy || !url}>+ Add</button>
    </div>
  {/if}
</div>

<div class="lbl mb-2">Outlier bands</div>
<div class="card max-w-[600px]">
  <p class="m mb-3">Read-only — bands are a global setting, applied identically to every niche.</p>
  {#each BAND_ORDER as [key, label, cls] (key)}
    <div class="kv"><span>{label}</span><span class="font-mono text-[12px] {cls}">{bandValue(key)}</span></div>
  {/each}
  <p class="m text-[10.5px] mt-3 leading-relaxed">
    {#if bands && bands.basis === 'percentile'}
      Percentile recalibrates per channel and per format, so ~10% of each channel lands in the tail.
    {:else}
      Fixed multiples of the median (legacy basis).
    {/if}
  </p>
</div>
