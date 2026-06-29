<script lang="ts">
  import { dwell } from './dwell.svelte';
</script>

{#if dwell.branches.length}
  <div class="branches">
    <span class="dim">Where next?</span>
    {#each dwell.branches as b (b.plan_id)}
      <button
        class="b"
        class:ready={b.ready}
        disabled={dwell.busy}
        onclick={() => dwell.requestAdvance({ action: 'plan', plan_id: b.plan_id })}
        title={b.ready ? 'already rendered — instant' : ''}
      >
        {b.mode === 'dwell' ? '↻ Dwell here' : '→ ' + b.title}
      </button>
    {/each}
  </div>
{/if}

<style>
  .branches { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; padding: 8px 16px 2px; }
  .b { background: var(--opt); text-align: left; max-width: 280px; line-height: 1.3; }
  .b.ready { color: var(--ready); }
</style>
