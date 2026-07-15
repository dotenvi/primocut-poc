<script>
  // Dependency-free line chart (matches the app's no-chart-library convention).
  // Stretches to fill its container width at a fixed pixel height; a
  // non-scaling stroke keeps the line even despite the horizontal stretch.
  export let values = [] // array of numbers (chronological)
  export let height = 120

  const W = 600 // viewBox width — arbitrary; the SVG scales to the container.
  $: pts = values.filter((v) => v != null)
  $: min = pts.length ? Math.min(...pts) : 0
  $: max = pts.length ? Math.max(...pts) : 1
  $: span = max - min || 1
  $: coords = pts.map((v, i) => {
    const x = pts.length > 1 ? (i / (pts.length - 1)) * W : W / 2
    const y = height - ((v - min) / span) * (height - 8) - 4
    return [x, y]
  })
  $: line = coords.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')
  $: area = coords.length ? `0,${height} ${line} ${W},${height}` : ''
</script>

{#if pts.length < 2}
  <p class="muted">Not enough snapshots yet to plot a trend.</p>
{:else}
  <svg
    class="sparkline"
    viewBox={`0 0 ${W} ${height}`}
    style="height:{height}px"
    preserveAspectRatio="none"
    role="img"
    aria-label="time series"
  >
    <polygon class="sparkline-area" points={area} />
    <polyline class="sparkline-line" points={line} vector-effect="non-scaling-stroke" />
  </svg>
{/if}
