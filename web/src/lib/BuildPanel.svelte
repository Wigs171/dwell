<script lang="ts">
  // The ingest-swarm build screen: live per-source progress + a Stop control.
  import { dwell } from './dwell.svelte';

  const running = $derived(dwell.buildStatus === 'running');
  const stopped = $derived(dwell.buildStatus === 'cancelled');
  const stream = $derived(dwell.buildLog.slice(-7));
  const cost = $derived(dwell.buildCost);
  const summary = $derived.by(() => {
    const c = { done: 0, failed: 0, skipped: 0, remaining: 0 };
    for (const s of dwell.buildSources) {
      if (s.status === 'done') c.done++;
      else if (s.status === 'failed') c.failed++;
      else if (s.status === 'skipped') c.skipped++;
      else if (s.status === 'queued' || s.status === 'cancelled') c.remaining++;
    }
    return c;
  });
  // Anything not finished can be resumed: the interrupted source + not-yet-started ones.
  const resumable = $derived(dwell.buildSources.some((s) => ['queued', 'cancelled', 'failed'].includes(s.status)));
  const heading = $derived(
    dwell.buildStatus === 'running' ? 'Building…'
    : dwell.buildStatus === 'done' ? 'Build complete'
    : dwell.buildStatus === 'cancelled' ? 'Build stopped'
    : dwell.buildStatus === 'error' ? 'Build finished with errors'
    : 'Build',
  );

  function openInRead() {
    dwell.buildClose();
    dwell.learnDiscard();
    dwell.page = 'read';
  }
</script>

<div class="build">
  <div class="head">
    <h1>{heading}</h1>
    {#if running}<span class="working">working</span>{/if}
    <span class="cost" title="estimated cost so far">${cost.toFixed(4)}</span>
  </div>
  <p class="roles" title="which model each agent uses for this build">
    <span class="role"><span class="rk">planner</span> {dwell.buildModels.orchestrator}</span>
    <span class="role"><span class="rk">writer</span> {dwell.buildModels.writer}</span>
  </p>
  {#if running && dwell.buildActivity}
    <p class="activity"><span class="spinner"></span>{dwell.buildActivity}</p>
  {:else if !running}
    <p class="summary">{summary.done} ingested{summary.skipped ? ` · ${summary.skipped} skipped` : ''}{summary.failed ? ` · ${summary.failed} failed` : ''}{summary.remaining ? ` · ${summary.remaining} not finished` : ''} · ${cost.toFixed(4)}</p>
    {#if stopped}
      <p class="explain">Stopped the source that was ingesting. Pages already written are saved; the rest weren’t touched. <strong>Resume</strong> finishes what’s left — already-ingested sources are skipped, the interrupted one re-runs from the top.</p>
    {/if}
  {/if}

  <ul class="srcs">
    {#each dwell.buildSources as s (s.id)}
      <li><span class="dot {s.status}"></span><span class="nm">{s.name}</span><span class="st {s.status}">{s.status}</span></li>
    {/each}
  </ul>

  {#if running && stream.length}
    <div class="stream">
      {#each stream as line, i (i)}<div class="ln" class:last={i === stream.length - 1}>{line}</div>{/each}
    </div>
  {/if}

  <div class="actions">
    {#if running}
      <button class="stop" onclick={() => dwell.stopBuild()}>Stop</button>
      <span class="note">Stop halts the source ingesting right now — finished sources are kept and you can resume. (It can take a while; pages stream in as they’re written.)</span>
    {:else}
      {#if resumable}
        <button class="primary" onclick={() => dwell.resumeBuild()}>Resume</button>
      {/if}
      <button class="ghost" onclick={() => dwell.buildClose()}>Back</button>
      <button class="{resumable ? 'ghost' : 'primary'}" onclick={openInRead}>Open in Read</button>
    {/if}
  </div>
</div>

<style>
  .build { max-width: 620px; margin: 0 auto; }
  .head { display: flex; align-items: baseline; gap: 12px; margin-bottom: 4px; }
  h1 { font-size: 24px; font-weight: 700; margin: 0; color: var(--fg); }
  .working { font-size: 12px; color: var(--accent); }
  .working::after { content: '…'; animation: dots 1.2s steps(4, end) infinite; }
  @keyframes dots { 0% { opacity: .3; } 50% { opacity: 1; } 100% { opacity: .3; } }
  .cost {
    margin-left: auto; font-family: 'Consolas', ui-monospace, monospace; font-size: 13px;
    font-variant-numeric: tabular-nums; color: var(--fg); background: var(--panel);
    border: 1px solid var(--border); border-radius: 7px; padding: 3px 9px;
  }
  .roles { display: flex; gap: 14px; margin: 4px 0 0; font-size: 11.5px; color: var(--meta); }
  .role { display: inline-flex; align-items: baseline; gap: 5px; }
  .rk { font-size: 9px; text-transform: uppercase; letter-spacing: .1em; color: var(--meta); opacity: .7; }
  .role { color: var(--fg); }
  .summary { font-size: 13px; color: var(--meta); margin: 8px 0 6px; }
  .explain { font-size: 12.5px; line-height: 1.55; color: var(--meta); margin: 0 0 18px; }
  .explain strong { color: var(--fg); }
  .activity { display: flex; align-items: center; gap: 9px; font-size: 13.5px; color: var(--fg); margin: 10px 0 4px; }
  .spinner { width: 11px; height: 11px; border-radius: 50%; flex-shrink: 0; border: 2px solid var(--border); border-top-color: var(--accent); animation: spin .8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  .srcs { list-style: none; margin: 14px 0 0; padding: 0; display: flex; flex-direction: column; gap: 5px; }
  .srcs li { display: flex; align-items: center; gap: 10px; padding: 8px 11px; background: var(--panel); border: 1px solid var(--border); border-radius: 9px; }
  .dot { width: 9px; height: 9px; border-radius: 50%; flex-shrink: 0; background: var(--border); }
  .dot.ingesting { background: var(--accent); animation: pulse 1s ease-in-out infinite; }
  .dot.done { background: #3fb950; }
  .dot.failed { background: var(--err); }
  .dot.skipped { background: color-mix(in srgb, var(--meta) 60%, transparent); }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: .35; } }
  .nm { flex: 1; font-size: 13px; color: var(--fg); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .st { font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: var(--meta); flex-shrink: 0; }
  .st.done { color: #3fb950; }
  .st.failed { color: var(--err); }
  .st.ingesting { color: var(--accent); }

  .stream { margin-top: 14px; font-family: 'Consolas', ui-monospace, monospace; font-size: 11.5px; background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 8px 11px; display: flex; flex-direction: column; gap: 2px; }
  .stream .ln { color: var(--meta); opacity: .55; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .stream .ln.last { color: var(--fg); opacity: 1; }

  .actions { display: flex; align-items: center; gap: 12px; margin-top: 24px; flex-wrap: wrap; }
  .stop { background: var(--err); border: 1px solid var(--err); color: #fff; font-weight: 650; font-size: 14px; padding: 10px 20px; border-radius: 10px; cursor: pointer; }
  .stop:hover { filter: brightness(1.08); }
  .primary { background: var(--accent); border: 1px solid var(--accent); color: var(--bg); font-weight: 650; font-size: 14px; padding: 10px 20px; border-radius: 10px; cursor: pointer; }
  .primary:hover { filter: brightness(1.08); }
  .ghost { background: none; border: 1px solid var(--border); color: var(--fg); font-size: 13px; padding: 9px 16px; border-radius: 9px; cursor: pointer; }
  .ghost:hover { border-color: var(--accent); }
  .note { font-size: 11.5px; color: var(--meta); }
</style>
