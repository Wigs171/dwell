<script lang="ts">
  import { dwell } from './dwell.svelte';

  let steerText = $state('');

  function doSteer() {
    const t = steerText.trim();
    if (!t) return;
    steerText = '';
    void dwell.steer(t);
  }
</script>

<div class="transport">
  <button class="play" class:on={dwell.narrating} onclick={() => dwell.togglePlay()}
          disabled={!dwell.pages.length}
          title="read aloud and flow to the next page when it finishes (a queued direction, else the default path)">
    {dwell.narrating ? '⏸ Pause' : '▶ Play'}
  </button>
  <input
    type="text" class="grow" bind:value={steerText} disabled={dwell.busy}
    placeholder="Steer with a phrase (e.g. &quot;toward Kepler&quot;)…  ↵"
    onkeydown={(e) => { if (e.key === 'Enter') doSteer(); }}
  />
  <button onclick={doSteer} disabled={dwell.busy || !steerText.trim()}>Steer ↳</button>
  <button onclick={() => dwell.newThread()} disabled={dwell.busy}>↻ New thread</button>
</div>

<style>
  .transport { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding: 6px 16px; }
  .grow { flex: 1 1 200px; min-width: 120px; }
  .play { flex: 0 0 auto; }
  .play.on { background: var(--accent); color: var(--accent-ink); }
</style>
