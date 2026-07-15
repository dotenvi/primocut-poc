<script>
  import Submit from './views/Submit.svelte'
  import Videos from './views/Videos.svelte'
  import Report from './views/Report.svelte'
  import Competitors from './views/Competitors.svelte'

  let route = location.hash.slice(1) || '/'
  window.addEventListener('hashchange', () => {
    route = location.hash.slice(1) || '/'
  })
</script>

<aside class="sidebar">
  <a href="#/" class="brand">
    <img src="/logo.png" alt="" class="brand-logo" />
    PRIMOCUT
  </a>
  <nav>
    <a href="#/" class:active={route === '/'}>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
        <polyline points="17 8 12 3 7 8" />
        <line x1="12" y1="3" x2="12" y2="15" />
      </svg>
      Submit
    </a>
    <a href="#/videos" class:active={route === '/videos'}>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="m22 8-6 4 6 4V8Z" />
        <rect x="2" y="6" width="14" height="12" rx="2" ry="2" />
      </svg>
      Videos
    </a>
    <a href="#/projects" class:active={route.startsWith('/projects')}>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <line x1="18" y1="20" x2="18" y2="10" />
        <line x1="12" y1="20" x2="12" y2="4" />
        <line x1="6" y1="20" x2="6" y2="14" />
      </svg>
      Competitors
    </a>
  </nav>
</aside>

<main class:report={route.startsWith('/report/')} class:projects={route.startsWith('/projects')}>
  {#if route.startsWith('/report/')}
    <Report id={route.split('/')[2]} />
  {:else if route.startsWith('/projects')}
    <Competitors {route} />
  {:else if route === '/videos'}
    <Videos />
  {:else}
    <Submit />
  {/if}
</main>
