<script lang="ts">
  // Tier-2 view: a chronological traversal of the vault, built from the enrichment
  // temporal sidecar (cli.py enrich → /timeline). Click an event to start a thread
  // at that node. The first consumer of the universal-ingest enrichment.
  import { dwell } from './dwell.svelte';
  import type { TimelineEvent } from './types';

  const fmtYear = (y: number) => (y < 0 ? `${Math.abs(y)} BCE` : `${y} CE`);
  // Insert a faint era divider where the list crosses BCE → CE.
  function crossesEra(events: TimelineEvent[], i: number): boolean {
    return i > 0 && events[i - 1].year < 0 && events[i].year >= 0;
  }
</script>

{#if dwell.timeline}
  <div class="overlay" role="presentation" onclick={(e) => { if (e.target === e.currentTarget) dwell.hideTimeline(); }}>
    <div class="win" role="dialog" aria-label="Timeline">
      <div class="titlebar">
        <span class="title">🕒 Timeline{dwell.timeline.topic ? ' · ' + dwell.timeline.topic : ''}</span>
        <button class="x" onclick={() => dwell.hideTimeline()} title="close">✕</button>
      </div>
      <div class="body">
        {#if !dwell.timeline.available}
          <div class="muted">{dwell.timeline.note ?? 'No timeline data.'}<br/>Run <code>cli.py enrich --vault &lt;path&gt;</code> to build it.</div>
        {:else if !dwell.timeline.events.length}
          <div class="muted">No dated events above the confidence threshold.</div>
        {:else}
          <div class="meta">{dwell.timeline.count} events · earliest → latest · click to jump</div>
          <ol class="tl">
            {#each dwell.timeline.events as ev, i (ev.page + ev.text + i)}
              {#if crossesEra(dwell.timeline.events, i)}<li class="era" aria-hidden="true"><span>CE</span></li>{/if}
              <li class="ev">
                <button class="row" onclick={() => dwell.jumpToEvent(ev.page)} title="Start a thread at {ev.title}">
                  <span class="when">{ev.text}</span>
                  <span class="dot" class:period={ev.kind === 'period'}></span>
                  <span class="what">{ev.title}</span>
                </button>
              </li>
            {/each}
          </ol>
        {/if}
      </div>
    </div>
  </div>
{/if}

<style>
  .overlay { position: fixed; inset: 0; background: #0006; z-index: 90; display: flex; align-items: center; justify-content: center; padding: 16px; }
  .win { width: min(620px, 100%); max-height: 86vh; display: flex; flex-direction: column;
    background: var(--bg); border: 1px solid var(--border); border-radius: 12px; box-shadow: 0 16px 60px #000a; overflow: hidden; }
  .titlebar { display: flex; align-items: center; justify-content: space-between; padding: 9px 12px; border-bottom: 1px solid var(--border); background: var(--panel); }
  .title { font-weight: 600; font-size: 13px; }
  .x { background: none; color: var(--meta); padding: 2px 7px; }
  .x:hover { background: var(--hover); color: var(--fg); }
  .body { padding: 12px 14px; overflow-y: auto; }
  .meta { color: var(--meta); font-size: 12px; margin: 0 0 10px; }
  .muted { color: var(--meta); font-style: italic; font-size: 13px; line-height: 1.6; }
  .muted code { font-style: normal; background: var(--panel); padding: 1px 5px; border-radius: 4px; }

  .tl { list-style: none; margin: 0; padding: 0; position: relative; }
  /* the spine */
  .tl::before { content: ''; position: absolute; left: 132px; top: 4px; bottom: 4px; width: 2px; background: var(--border); }
  .ev .row {
    display: grid; grid-template-columns: 120px 14px 1fr; align-items: baseline; gap: 8px;
    width: 100%; text-align: left; background: none; border: none; padding: 5px 6px; border-radius: 7px;
    color: var(--fg); cursor: pointer; font: inherit;
  }
  .ev .row:hover { background: var(--hover, color-mix(in srgb, var(--accent) 12%, transparent)); }
  .when { color: var(--accent); font-family: Consolas, monospace; font-size: 12px; text-align: right; white-space: nowrap; }
  .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--accent); justify-self: center; position: relative; z-index: 1;
    box-shadow: 0 0 0 3px var(--bg); }
  .dot.period { border-radius: 2px; }   /* periods are squares, dates are dots */
  .what { font-size: 13.5px; line-height: 1.35; }
  .ev .row:hover .what { color: var(--accent); }

  .era { display: grid; grid-template-columns: 120px 14px 1fr; gap: 8px; margin: 4px 0; }
  .era span { grid-column: 2 / 4; font-size: 10.5px; font-weight: 700; letter-spacing: 0.1em; color: var(--meta); }
</style>
