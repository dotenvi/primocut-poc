<script>
  import { api } from '../lib/api.js'

  let url = ''
  let error = ''
  let busy = false

  async function go() {
    if (!url || busy) return
    busy = true
    error = ''
    try {
      const res = await api.submit(url)
      // go straight to the report and follow the pipeline filling in live
      location.hash = `#/report/${res.video_id}`
    } catch (e) {
      error = e.message
    } finally {
      busy = false
    }
  }
</script>

<section class="card">
  <h1>Submit a YouTube live</h1>
  <p class="muted">Paste the URL of a live/video. Processing runs in the background.</p>
  <div class="row-input">
    <input
      bind:value={url}
      placeholder="https://www.youtube.com/watch?v=..."
      on:keydown={(e) => e.key === 'Enter' && go()}
    />
    <button on:click={go} disabled={busy || !url}>{busy ? 'Submitting…' : 'Submit'}</button>
  </div>
  {#if error}<p class="error">{error}</p>{/if}
</section>
