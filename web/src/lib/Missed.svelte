<script lang="ts">
  import { dwell } from './dwell.svelte';
</script>

{#if dwell.missed}
  <div class="overlay" role="presentation" onclick={(e) => { if (e.target === e.currentTarget) dwell.hideMissed(); }}>
    <div class="win" role="dialog" aria-label="Missed connections">
      <div class="titlebar">
        <span class="title">✧ Missed connections</span>
        <button class="x" onclick={() => dwell.hideMissed()} title="close">✕</button>
      </div>
      <div class="body">
        <div class="card">
          <h2>{dwell.missed.embed_label} · close but not linked</h2>
          {#if !dwell.missed.pairs.length}
            <div class="muted">none above threshold (or embeddings unavailable / TF-IDF active)</div>
          {/if}
          {#each dwell.missed.pairs as p}
            <div class="pair">
              <span class="sim">{p.sim.toFixed(3)}</span>
              {p.title_a} <span class="dim">⇿</span> {p.title_b}
            </div>
          {/each}
        </div>
      </div>
    </div>
  </div>
{/if}

<style>
  .overlay { position: fixed; inset: 0; background: #0006; z-index: 90; display: flex; align-items: center; justify-content: center; padding: 16px; }
  .win { width: min(560px, 100%); max-height: 84vh; display: flex; flex-direction: column;
    background: var(--bg); border: 1px solid var(--border); border-radius: 12px; box-shadow: 0 16px 60px #000a; overflow: hidden; }
  .titlebar { display: flex; align-items: center; justify-content: space-between; padding: 9px 12px; border-bottom: 1px solid var(--border); background: var(--panel); }
  .title { font-weight: 600; font-size: 13px; }
  .x { background: none; color: var(--meta); padding: 2px 7px; }
  .x:hover { background: var(--hover); color: var(--fg); }
  .body { padding: 12px; overflow-y: auto; }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 12px; }
  .card h2 { font-size: 12px; font-weight: 600; color: var(--meta); margin: 0 0 8px; padding-bottom: 6px; border-bottom: 1px solid color-mix(in srgb, var(--border) 40%, transparent); }
  .muted { color: var(--meta); font-style: italic; font-size: 13px; }
  .pair { padding: 6px 0; border-bottom: 1px solid var(--line); font-size: 13px; }
  .pair:last-child { border-bottom: none; }
  .sim { color: var(--accent); font-family: Consolas, monospace; margin-right: 6px; }
  .dim { color: var(--meta); }
</style>
