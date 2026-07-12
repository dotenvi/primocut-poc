<script>
  import Submit from './views/Submit.svelte'
  import Videos from './views/Videos.svelte'
  import Report from './views/Report.svelte'

  let route = location.hash.slice(1) || '/'
  window.addEventListener('hashchange', () => {
    route = location.hash.slice(1) || '/'
  })
</script>

<header class="topbar">
  <a href="#/" class="brand">PRIMOCUT</a>
  <nav>
    <a href="#/">Enviar</a>
    <a href="#/videos">Vídeos</a>
  </nav>
</header>

<main>
  {#if route.startsWith('/report/')}
    <Report id={route.split('/')[2]} />
  {:else if route === '/videos'}
    <Videos />
  {:else}
    <Submit />
  {/if}
</main>
