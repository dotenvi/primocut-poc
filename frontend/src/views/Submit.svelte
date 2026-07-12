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
      // vai direto para o relatório e acompanha a pipeline preenchendo ao vivo
      location.hash = `#/report/${res.video_id}`
    } catch (e) {
      error = e.message
    } finally {
      busy = false
    }
  }
</script>

<section class="card">
  <h1>Enviar live do YouTube</h1>
  <p class="muted">Cole a URL de uma live/vídeo. O processamento roda em segundo plano.</p>
  <div class="row-input">
    <input
      bind:value={url}
      placeholder="https://www.youtube.com/watch?v=..."
      on:keydown={(e) => e.key === 'Enter' && go()}
    />
    <button on:click={go} disabled={busy || !url}>{busy ? 'Enviando…' : 'Enviar'}</button>
  </div>
  {#if error}<p class="error">{error}</p>{/if}
</section>
