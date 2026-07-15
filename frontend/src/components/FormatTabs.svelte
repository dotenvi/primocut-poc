<script>
  // Shorts and long-form are different products (monetisation, audience, format),
  // so every population is reported apart. This is the one control that picks
  // which of the two a section shows; bind:value to read the choice.
  export let value = 'long'
  export let counts = null // optional { long, short } shown next to each label

  const TABS = [
    ['long', 'Long-form'],
    ['short', 'Shorts'],
  ]
</script>

<div class="tab-bar flush" role="tablist">
  {#each TABS as [key, label] (key)}
    <button
      class="tab"
      class:active={value === key}
      role="tab"
      aria-selected={value === key}
      on:click={() => (value = key)}
    >
      {label}{#if counts}<span class="tab-count muted">{counts[key] ?? 0}</span>{/if}
    </button>
  {/each}
</div>

<style>
  .tab-count { margin-left: 6px; font-variant-numeric: tabular-nums; }
</style>
