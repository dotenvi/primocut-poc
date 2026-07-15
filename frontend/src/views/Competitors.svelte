<script>
  import { onMount } from 'svelte'
  import { api } from '../lib/api.js'
  import ProjectSwitcher from '../components/ProjectSwitcher.svelte'
  import ProjectDashboard from './ProjectDashboard.svelte'
  import ProjectChannels from './ProjectChannels.svelte'
  import ChannelDashboard from './ChannelDashboard.svelte'
  import VideoDetail from './VideoDetail.svelte'

  export let route // '/projects' | '/projects/:pid' | '/projects/:pid/channels[/:cid[/videos/:vid]]'

  let projects = null // null while loading
  let error = ''

  // ['projects', pid, 'channels', cid, 'videos', vid]
  $: parts = route.split('/').filter(Boolean)
  $: pid = parts[1] || ''
  $: cid = parts[2] === 'channels' ? parts[3] || '' : ''
  $: vid = parts[4] === 'videos' ? parts[5] || '' : ''
  $: tab = parts[2] === 'channels' && !cid ? 'channels' : 'dashboard'

  async function loadProjects() {
    try {
      projects = await api.projects()
      error = ''
    } catch (e) {
      error = e.message
      projects = projects || []
    }
  }
  onMount(loadProjects)

  // No project in the route yet — land on the first one (selection lives in the URL).
  $: if (projects && projects.length && !pid) location.hash = `#/projects/${projects[0].id}`

  async function onChanged(e) {
    const removed = e.detail && e.detail.removed
    await loadProjects()
    if (removed && String(removed) === String(pid)) {
      location.hash = projects.length ? `#/projects/${projects[0].id}` : '#/projects'
    }
  }
</script>

<section>
  <h1>Competitors</h1>
  {#if error}<p class="error">{error}</p>{/if}

  {#if projects === null}
    <p class="muted">Loading…</p>
  {:else}
    <ProjectSwitcher {projects} {pid} open={projects.length === 0} on:changed={onChanged} />

    {#if projects.length === 0}
      <p class="muted">No projects yet — create one above to start tracking competitor channels.</p>
    {:else if pid}
      <!-- Remount on any route-identity change: the views load on mount. -->
      {#key `${pid}/${cid}/${vid}`}
        {#if vid}
          <VideoDetail {pid} {cid} {vid} />
        {:else if cid}
          <ChannelDashboard {pid} {cid} />
        {:else}
          <div class="tab-bar flush" role="tablist">
            <button
              class="tab"
              class:active={tab === 'dashboard'}
              role="tab"
              aria-selected={tab === 'dashboard'}
              on:click={() => (location.hash = `#/projects/${pid}`)}
            >Dashboard</button>
            <button
              class="tab"
              class:active={tab === 'channels'}
              role="tab"
              aria-selected={tab === 'channels'}
              on:click={() => (location.hash = `#/projects/${pid}/channels`)}
            >Channels</button>
          </div>
          {#if tab === 'channels'}
            <ProjectChannels {pid} />
          {:else}
            <ProjectDashboard {pid} />
          {/if}
        {/if}
      {/key}
    {/if}
  {/if}
</section>
