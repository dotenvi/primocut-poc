<script>
  // The "channel spread" strip: a tiny histogram of the channel-format window's
  // view counts, with the bar containing THIS video highlighted. Same component
  // in the Radar cards and the video detail — the payload is identical, only the
  // marked value differs. Ports the prototype's bar builder (v3, lines 533-542).
  export let buckets = [] // per-bucket counts
  export let edges = [] // len = buckets.length + 1
  export let markViews = null // this video's view count → the highlighted bar
  export let height = 24 // px
  export let label = 'CHANNEL SPREAD'

  $: max = buckets.length ? Math.max(...buckets, 1) : 1

  // Which bar holds `markViews`, via the bucket edges. -1 when there is nothing
  // to mark (no view count, or it falls outside the observed range).
  $: mark = (() => {
    if (markViews == null || edges.length < 2) return -1
    for (let i = 0; i < buckets.length; i++) {
      const lo = edges[i]
      const hi = edges[i + 1]
      if (markViews >= lo && (markViews < hi || i === buckets.length - 1)) return i
    }
    return -1
  })()
</script>

{#if buckets.length}
  <div class="flex flex-col items-end gap-1 shrink-0">
    <div class="flex items-end gap-px" style="height:{height}px" role="img" aria-label="{label} — {buckets.length} bars">
      {#each buckets as v, i (i)}
        <i
          class="block w-0.5 {i === mark ? 'bg-heat-99' : 'bg-[#2B323A]'}"
          style="height:{Math.max(1, Math.round((v / max) * height))}px; min-height:{i === mark ? 3 : 1}px"
        ></i>
      {/each}
    </div>
    <span class="font-mono text-[9px] text-ink-3 tracking-wider">{label}</span>
  </div>
{/if}
