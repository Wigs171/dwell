<script lang="ts">
  import { dwell } from './dwell.svelte';
  const icon: Record<string, string> = { resume: '▸', new: '✦', surprise: '🎲' };
</script>

<div class="menu">
  <span class="dim">Begin:</span>
  {#each dwell.session?.menu ?? [] as m (m.key)}
    <button class="m" onclick={() => dwell.begin(m.key)}>
      <span class="label">{icon[m.key] ?? ''} {m.label}</span>
      <span class="hint">{m.hint}</span>
    </button>
  {/each}
</div>

<style>
  .menu { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; padding: 12px 16px; }
  .m { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; padding: 8px 14px; }
  .label { font-size: 14px; }
  .hint { color: var(--meta); font-size: 11px; }
</style>
