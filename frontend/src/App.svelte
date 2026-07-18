<script>
  import { parseRoute } from './lib/router.js'
  import Sidebar from './components/Sidebar.svelte'
  import Radar from './views/Radar.svelte'
  import Activity from './views/Activity.svelte'
  import Lives from './views/Lives.svelte'
  import CutRoom from './views/CutRoom.svelte'
  import Niche from './views/Niche.svelte'
  import VideoDetail from './views/VideoDetail.svelte'
  import ChannelDashboard from './views/ChannelDashboard.svelte'
  import Settings from './views/Settings.svelte'

  let route = parseRoute(location.hash)
  window.addEventListener('hashchange', () => {
    route = parseRoute(location.hash)
    window.scrollTo(0, 0)
  })

  // Identity that, when it changes, should remount the view (views load on mount).
  $: key = `${route.view}/${route.pid || ''}/${route.lid || route.vid || route.cid || ''}`

  // Width policy: the canvas is generous by default so large screens are used;
  // only reading/table-oriented views opt back into the comfortable measure
  // (long lines of prose or wide tables read worse when they sprawl).
  const READING_VIEWS = new Set([
    'radar', 'activity', 'lives', 'niche', 'video', 'channel', 'settings',
  ])
  $: contentWidth = READING_VIEWS.has(route.view) ? 'max-w-[1160px]' : 'max-w-[1680px]'
</script>

<div class="grid grid-cols-[236px_1fr] min-h-screen max-[960px]:grid-cols-1">
  <Sidebar {route} />

  <main class="px-7 pt-6 pb-16 w-full {contentWidth}">
    {#key key}
      {#if route.view === 'radar'}
        <Radar />
      {:else if route.view === 'activity'}
        <Activity />
      {:else if route.view === 'lives'}
        <Lives pid={route.pid} />
      {:else if route.view === 'cutroom'}
        <CutRoom pid={route.pid} lid={route.lid} />
      {:else if route.view === 'niche'}
        <Niche pid={route.pid} tab={route.tab} />
      {:else if route.view === 'video'}
        <VideoDetail pid={route.pid} cid={route.cid} vid={route.vid} />
      {:else if route.view === 'channel'}
        <ChannelDashboard pid={route.pid} cid={route.cid} />
      {:else if route.view === 'settings'}
        <Settings pid={route.pid} />
      {/if}
    {/key}
  </main>
</div>
