<script>
  import { createEventDispatcher } from 'svelte'
  import { api } from '../lib/api.js'

  export let projects = []
  export let pid = ''
  export let open = false // force the manage panel open (e.g. when there are no projects yet)

  const dispatch = createEventDispatcher()

  let managing = false
  $: if (open) managing = true

  let error = ''
  let busy = false
  let newName = ''
  let newDesc = ''
  let renaming = null // id of the project being renamed
  let renameName = ''

  function selectProject(e) {
    const id = e.target.value
    if (id) location.hash = `#/projects/${id}`
  }

  async function create() {
    if (!newName.trim() || busy) return
    busy = true
    error = ''
    try {
      const p = await api.createProject({ name: newName.trim(), description: newDesc.trim() })
      newName = ''
      newDesc = ''
      dispatch('changed', { pid: p.id })
      location.hash = `#/projects/${p.id}`
      managing = false
    } catch (e) {
      error = e.message
    } finally {
      busy = false
    }
  }

  function startRename(p) {
    renaming = p.id
    renameName = p.name
  }

  async function saveRename(p) {
    if (!renameName.trim() || busy) return
    busy = true
    error = ''
    try {
      await api.patchProject(p.id, { name: renameName.trim() })
      renaming = null
      dispatch('changed', {})
    } catch (e) {
      error = e.message
    } finally {
      busy = false
    }
  }

  async function remove(p) {
    if (!confirm(`Remove project “${p.name}”? Channels are detached; all channel data is kept.`)) return
    busy = true
    error = ''
    try {
      await api.deleteProject(p.id)
      dispatch('changed', { removed: p.id })
    } catch (e) {
      error = e.message
    } finally {
      busy = false
    }
  }
</script>

<div class="switcher">
  <div class="switcher-bar">
    {#if projects.length}
      <select class="proj-select" on:change={selectProject} aria-label="Select project">
        {#each projects as p (p.id)}
          <option value={p.id} selected={String(p.id) === String(pid)}>{p.name}</option>
        {/each}
      </select>
    {/if}
    <button class="mini" class:active={managing} on:click={() => (managing = !managing)}>⚙ Manage</button>
  </div>

  {#if managing}
    <div class="card manage-panel">
      {#if error}<p class="error">{error}</p>{/if}
      {#if projects.length}
        <h3>Projects</h3>
        <ul class="list">
          {#each projects as p (p.id)}
            <li class="row">
              {#if renaming === p.id}
                <input bind:value={renameName} on:keydown={(e) => e.key === 'Enter' && saveRename(p)} />
                <button class="mini" on:click={() => saveRename(p)} disabled={busy}>Save</button>
                <button class="mini" on:click={() => (renaming = null)}>Cancel</button>
              {:else}
                <span class="grow">{p.name} <span class="muted">· {p.channel_count ?? 0} channels</span></span>
                <button class="mini" on:click={() => startRename(p)}>Rename</button>
                <button class="mini danger" on:click={() => remove(p)} disabled={busy}>Remove</button>
              {/if}
            </li>
          {/each}
        </ul>
      {/if}
      <h3>{projects.length ? 'New project' : 'Create your first project'}</h3>
      <div class="row-input">
        <input bind:value={newName} placeholder="Project name" on:keydown={(e) => e.key === 'Enter' && create()} />
      </div>
      <div class="row-input">
        <input bind:value={newDesc} placeholder="Description (optional)" />
        <button on:click={create} disabled={busy || !newName.trim()}>{busy ? 'Saving…' : 'Create'}</button>
      </div>
    </div>
  {/if}
</div>
