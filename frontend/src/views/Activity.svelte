<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import { hhmm } from '../lib/format.js'

  let rows = []
  let cursor = null
  let more = false
  let quota = null
  let error = ''
  let loaded = false

  async function load(before = null) {
    try {
      const r = await api.activity({ limit: 50, before_id: before })
      rows = before ? [...rows, ...r.events] : r.events
      cursor = r.next_before_id
      more = r.events.length === 50 && r.next_before_id != null
      quota = r.quota_units
      error = ''
    } catch (e) {
      error = e.message
    } finally {
      loaded = true
    }
  }
  onMount(() => load())

  const job = (ev) => ev.type.replace(/_/g, ' ')
  const scope = (ev) => ev.title || ev.channel_title || (ev.payload || {}).scope || '—'
  const quotaOf = (ev) => {
    const q = (ev.payload || {}).quota_units
    return q == null ? '—' : q
  }
  const failed = (ev) => ev.severity === 'error' && !ev.resolved_at
  const resultLabel = (ev) => (failed(ev) ? (ev.payload || {}).stage || 'failed' : 'ok')
</script>

<div class="flex items-end justify-between gap-3 mb-5">
  <div><div class="eyebrow">System</div><h1>Activity</h1></div>
  {#if quota != null}
    <div class="m">{quota.toLocaleString()} quota units accounted</div>
  {/if}
</div>

{#if error}<p class="m text-err mb-4">{error}</p>{/if}

{#if !loaded}
  <p class="m">Loading…</p>
{:else if rows.length === 0}
  <p class="text-ink-2">No activity recorded yet.</p>
{:else}
  <table class="w-full border-collapse text-[13px]">
    <thead>
      <tr>
        <th class="w-16 text-right">Time</th>
        <th>Job</th>
        <th>Scope</th>
        <th class="text-right">Quota</th>
        <th class="text-right">Result</th>
      </tr>
    </thead>
    <tbody>
      {#each rows as ev (ev.id)}
        <tr class="hover:bg-[#15181C]">
          <td class="text-right font-mono text-[12px] text-ink-2">{hhmm(ev.occurred_at)}</td>
          <td>{job(ev)}</td>
          <td class="text-ink-2 truncate max-w-[360px]">{scope(ev)}</td>
          <td class="text-right font-mono text-[12px]" class:text-ink-3={quotaOf(ev) === '—'}>{quotaOf(ev)}</td>
          <td class="text-right">
            {#if failed(ev)}
              <span class="tag bg-err-bg text-err border-err-line">{resultLabel(ev)}</span>
            {:else}
              <span class="tag bg-ok-bg text-ok border-ok-line">ok</span>
            {/if}
          </td>
        </tr>
      {/each}
    </tbody>
  </table>

  {#if more}
    <div class="mt-4">
      <button class="btn" on:click={() => load(cursor)}>Load more</button>
    </div>
  {/if}
{/if}
