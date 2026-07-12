<script>
  import { onMount, onDestroy } from 'svelte'
  import { api, isActive } from '../lib/api.js'

  let videos = []
  let error = ''
  let timer

  async function load() {
    try {
      videos = await api.list()
      error = ''
    } catch (e) {
      error = e.message
    }
  }

  async function stop(id) {
    try { await api.stop(id); load() } catch (e) { error = e.message }
  }

  async function remove(id) {
    if (!confirm('Deletar TUDO sobre este vídeo? Isso é irreversível — para reprocessar você precisará reenviar a URL.')) return
    try { await api.remove(id); load() } catch (e) { error = e.message }
  }

  onMount(() => {
    load()
    timer = setInterval(load, 2000) // polling ao vivo
  })
  onDestroy(() => clearInterval(timer))
</script>

<section>
  <h1>Vídeos</h1>
  {#if error}<p class="error">{error}</p>{/if}
  {#if videos.length === 0}
    <p class="muted">Nenhum vídeo ainda. <a href="#/">Enviar um</a>.</p>
  {/if}
  <ul class="list">
    {#each videos as v (v.id)}
      <li class="row">
        <div class="grow">
          <a href={`#/report/${v.id}`}>{v.title || v.id}</a>
          <div class="muted">{v.channel}</div>
        </div>
        <span class="status status-{v.status}">{v.status}</span>
        <div class="bar" title={`${v.progress}%`}>
          <div class="fill" style="width:{v.progress}%"></div>
        </div>
        {#if isActive(v.status)}
          <button class="mini" on:click|preventDefault={() => stop(v.id)}>Interromper</button>
        {:else}
          <button class="mini danger" on:click|preventDefault={() => remove(v.id)}>Deletar</button>
        {/if}
      </li>
    {/each}
  </ul>
</section>
