<script>
  // Embedded YouTube player (official IFrame Player API). The video is NOT
  // downloaded — it uses YouTube's embed with the existing video_id. Exposes a
  // `seek(seconds)` method the parent calls to jump to a topic and play.
  import { onMount, onDestroy } from 'svelte'

  export let videoId
  // Optional: notified with the YT player state on every change (1 playing,
  // 2 paused, 0 ended, …). The Preview-cut driver uses it to react to buffering.
  export let onState = null

  let host // div that the API replaces with the <iframe>
  let player = null
  let ready = false
  let blocked = false // embedding disabled by the owner, or API failed to load
  let pendingSeek = null

  // Load the IFrame API script once, shared across the whole page.
  function loadApi() {
    return new Promise((resolve, reject) => {
      if (window.YT && window.YT.Player) return resolve(window.YT)
      const prev = window.onYouTubeIframeAPIReady
      window.onYouTubeIframeAPIReady = () => {
        if (typeof prev === 'function') prev()
        resolve(window.YT)
      }
      if (!document.getElementById('yt-iframe-api')) {
        const tag = document.createElement('script')
        tag.id = 'yt-iframe-api'
        tag.src = 'https://www.youtube.com/iframe_api'
        tag.onerror = () => reject(new Error('could not load the YouTube player'))
        document.head.appendChild(tag)
      }
    })
  }

  function doSeek(seconds) {
    player.seekTo(seconds, true)
    player.playVideo()
  }

  // Called by the parent. Returns false when the player is unavailable so the
  // caller can fall back to the external YouTube link.
  export function seek(seconds) {
    if (blocked || !player) {
      if (blocked) return false
      pendingSeek = seconds
      return true
    }
    if (ready) doSeek(seconds)
    else pendingSeek = seconds
    return true
  }

  // Playback controls for the virtual Preview cut (plays the composition by seek
  // only — no download, no encode). `getTime` lets the parent's driver detect the
  // end of an item and jump to the next. All are no-ops when the player is absent.
  export function play() { if (player && ready && !blocked) player.playVideo() }
  export function pause() { if (player && ready && !blocked) player.pauseVideo() }
  export function getTime() {
    return player && ready && !blocked ? player.getCurrentTime() : 0
  }

  onMount(async () => {
    try {
      const YT = await loadApi()
      player = new YT.Player(host, {
        videoId,
        playerVars: { rel: 0, playsinline: 1 },
        events: {
          onReady: () => {
            ready = true
            if (pendingSeek != null) {
              doSeek(pendingSeek)
              pendingSeek = null
            }
          },
          onStateChange: (e) => { if (typeof onState === 'function') onState(e.data) },
          // 101 / 150 = embedding disabled by the video owner.
          onError: (e) => {
            if (e && (e.data === 101 || e.data === 150)) blocked = true
          },
        },
      })
    } catch (e) {
      blocked = true
    }
  })

  onDestroy(() => {
    if (player && typeof player.destroy === 'function') player.destroy()
  })
</script>

{#if blocked}
  <div class="player-blocked">
    ⚠️ This video can't be embedded (the owner disabled it). Use the “Open on YouTube” links to watch.
  </div>
{/if}
<div class="player-frame" class:hidden={blocked}><div bind:this={host}></div></div>
