<script lang="ts">
  import { dwell } from './dwell.svelte';

  // recent nodes read this session (unique, most-recent first) — the reading trail
  const trail = $derived.by(() => {
    const m = new Map<string, string>();
    for (const p of dwell.pages) m.set(p.node, p.title);
    return [...m].reverse().slice(0, 8).map(([node, title]) => ({ node, title }));
  });
  const vaultName = $derived(
    (dwell.vaultPath.split(/[\\/]/).filter(Boolean).pop()) || 'Dwell',
  );
  // background ingest awareness — keep reading while a build runs; surface its outcome here
  const ingestRunning = $derived(dwell.buildStatus === 'running');
  const ingestNotice = $derived(dwell.buildNotice);   // null | 'done' | 'error' | 'capped'
</script>

<aside class="sidebar">
  <div class="brand">
    <span class="logo">◈</span><span class="name">Dwell</span>
    <span class="spacer"></span>
    <button class="icon" title="collapse" onclick={() => dwell.toggleSidebar()}>«</button>
  </div>

  <div class="inner">
    <nav class="nav">
      <button class="nav-item" class:active={dwell.page === 'home'} onclick={() => (dwell.page = 'home')}>Dwell</button>
      <button class="nav-item" class:active={dwell.page === 'read'} onclick={() => (dwell.page = 'read')}>Read</button>
      <button class="nav-item" class:active={dwell.page === 'learn'} onclick={() => dwell.openLearn()}>
        <span class="ni-label">Learn</span>
        {#if ingestRunning}<span class="working">working</span>
        {:else if ingestNotice === 'done'}<span class="badge ok" title="ingest finished">✓</span>
        {:else if ingestNotice}<span class="badge err" title="ingest needs attention">!</span>{/if}
      </button>
    </nav>
    {#if ingestNotice && ingestNotice !== 'done' && dwell.buildNoticeMsg}
      <button class="nav-alert" onclick={() => dwell.openLearn()} title="open Learn to act on it">{dwell.buildNoticeMsg}</button>
    {/if}

    {#if dwell.page === 'read'}
    {#if dwell.session}
      <button class="switch" onclick={() => dwell.backToGallery()} disabled={dwell.busy}
              title="back to the vault gallery">‹ Vaults</button>
      <div class="field">
        <input type="text" placeholder="Search nodes…" bind:value={dwell.query} />
      </div>
      {#if dwell.pathProgress}
        <div class="path-chip" class:done={dwell.pathProgress.complete}
             title={dwell.pathProgress.goal ?? ''}>
          <span class="pc-ico">◇</span>
          <span class="pc-body">
            <span class="pc-title">{dwell.pathProgress.title}</span>
            <span class="pc-step">step {dwell.pathProgress.gate}/{dwell.pathProgress.gates}{dwell.pathProgress.complete ? ' · done ✓' : ''}</span>
          </span>
        </div>
      {/if}
    {/if}

    {#if dwell.query.trim()}
      <div class="section">
        <div class="sec-head static">Results · {dwell.filteredNodes.length}</div>
        {#each dwell.filteredNodes as n (n.id)}
          <button class="list-item" disabled={dwell.busy}
                  onclick={() => { dwell.requestBeginAt(n.id); dwell.query = ''; }}>
            <span class="dot" class:seen={n.seen > 0}>●</span>
            <span class="t">{n.title}</span><span class="c">{n.centrality}</span>
          </button>
        {/each}
        {#if !dwell.filteredNodes.length}<div class="muted">no matches</div>{/if}
      </div>
    {:else}
      {#if dwell.session}
        <button class="gen-path" disabled={dwell.busy} onclick={() => dwell.generatePath()}
                title="wander the vault and compose a fresh guided path (different every time)">
          ✨ Generate a path
        </button>
      {/if}

      {#if dwell.paths.length}
        <div class="section">
          <div class="sec-head static">Guided paths</div>
          {#each dwell.paths as p (p.id)}
            <button class="list-item path" onclick={() => dwell.startPath(p.id)}
                    disabled={dwell.busy} title={p.goal || `walk this path (${p.gates} steps)`}>
              <span class="dot path-dot">◇</span>
              <span class="t">{p.title}</span><span class="c">{p.gates}</span>
            </button>
          {/each}
        </div>
      {/if}

      {#if trail.length}
        <div class="section">
          <div class="sec-head static">Reading trail</div>
          {#each trail as n (n.node)}
            <button class="list-item" disabled={dwell.busy} onclick={() => dwell.requestBeginAt(n.node)} title="return here">
              <span class="dot seen">●</span><span class="t">{n.title}</span>
            </button>
          {/each}
        </div>
      {/if}

      {#if dwell.popular.length}
        <div class="section">
          <button class="sec-head" onclick={() => dwell.toggleSection('popular')}>
            <span class="caret">{dwell.collapsed.popular ? '▸' : '▾'}</span> Popular nodes
          </button>
          {#if !dwell.collapsed.popular}
            {#each dwell.popular as n (n.id)}
              <button class="list-item" onclick={() => dwell.requestBeginAt(n.id)}
                      disabled={dwell.busy} title="start a thread here ({n.centrality} links)">
                <span class="dot" class:seen={n.seen > 0}>●</span>
                <span class="t">{n.title}</span><span class="c">{n.centrality}</span>
              </button>
            {/each}
          {/if}
        </div>
      {/if}

    {/if}
    {/if}
  </div>

  <!-- bottom user-bar: identity + the settings gear (Odysseus) -->
  <div class="user-bar">
    {#if dwell.session}
      <div class="ub-left" title={dwell.session.topic ?? ''}>
        <span class="ub-dot">◈</span>
        <span class="ub-name">{vaultName}</span>
      </div>
    {:else}
      <span class="ub-spacer"></span>
    {/if}
    {#if dwell.cost > 0}<span class="cost">${dwell.cost.toFixed(4)}</span>{/if}
    {#if dwell.session}<button class="gear" title="notes" onclick={() => (dwell.notesOpen = true)}>✎{#if dwell.notes.length}<sup>{dwell.notes.length}</sup>{/if}</button>{/if}
    <button class="gear" title="settings" onclick={() => (dwell.settingsOpen = true)}>⚙</button>
  </div>
</aside>

<style>
  .sidebar {
    width: 248px; flex: 0 0 248px; height: 100%;
    background: var(--panel); border-right: 1px solid var(--border);
    display: flex; flex-direction: column; min-height: 0;
  }
  .brand { display: flex; align-items: center; gap: 8px; padding: 12px 14px; border-bottom: 1px solid var(--border); }
  .logo { color: var(--accent); font-size: 16px; }
  .name { font-weight: 600; font-size: 15px; letter-spacing: .3px; }
  .icon { background: none; padding: 2px 6px; color: var(--meta); font-size: 15px; }
  .icon:hover { background: var(--hover); color: var(--fg); }

  .inner { flex: 1 1 auto; overflow-y: auto; padding: 10px; display: flex; flex-direction: column; gap: 6px; }

  .nav { display: flex; flex-direction: column; gap: 1px; }
  .nav-item {
    text-align: left; background: transparent; color: var(--fg); border: none; cursor: pointer;
    font-size: 13.5px; font-weight: 600; padding: 8px 10px; border-radius: 7px;
  }
  .nav-item:hover { background: var(--hover); }
  .nav-item.active { color: var(--accent); background: color-mix(in srgb, var(--accent) 13%, transparent); }
  .nav-item { display: flex; align-items: center; gap: 8px; }
  .ni-label { flex: 1 1 auto; }
  .working {
    font-size: 10px; color: var(--accent); text-transform: lowercase; letter-spacing: .04em;
    background: color-mix(in srgb, var(--accent) 14%, transparent); border-radius: 5px; padding: 1px 6px;
  }
  .working::after { content: '…'; animation: ndots 1.2s steps(4, end) infinite; }
  @keyframes ndots { 0% { opacity: .3; } 50% { opacity: 1; } 100% { opacity: .3; } }
  .badge { font-size: 11px; width: 16px; height: 16px; display: inline-flex; align-items: center; justify-content: center; border-radius: 50%; flex: 0 0 auto; font-weight: 700; }
  .badge.ok { color: #3fb950; background: color-mix(in srgb, #3fb950 18%, transparent); }
  .badge.err { color: var(--err); background: color-mix(in srgb, var(--err) 18%, transparent); }
  .nav-alert {
    text-align: left; width: 100%; margin-top: 2px; background: color-mix(in srgb, var(--err) 10%, transparent);
    border: 1px solid color-mix(in srgb, var(--err) 35%, transparent); color: var(--fg);
    border-radius: 7px; padding: 6px 9px; font-size: 11.5px; line-height: 1.4; cursor: pointer;
  }
  .nav-alert:hover { background: color-mix(in srgb, var(--err) 16%, transparent); }
  .field input { width: 100%; }
  .switch {
    width: 100%; text-align: left; background: transparent; color: var(--meta);
    border: 1px solid var(--border); border-radius: 6px; padding: 6px 10px; font-size: 12px;
  }
  .switch:hover:not(:disabled) { background: var(--hover); color: var(--fg); }
  .muted { color: var(--meta); font-size: 12px; padding: 6px; font-style: italic; }

  .section { margin-top: 6px; display: flex; flex-direction: column; gap: 1px; }
  .sec-head {
    display: flex; align-items: center; gap: 6px; width: 100%; text-align: left;
    background: transparent; color: var(--meta);
    font-size: 10px; text-transform: uppercase; letter-spacing: .12em; padding: 6px 6px; border-radius: 6px;
  }
  button.sec-head:hover { background: var(--hover); color: var(--fg); }
  .sec-head.static { pointer-events: none; }
  .caret { font-size: 9px; width: 9px; }

  .list-item {
    display: flex; align-items: center; gap: 7px; width: 100%; text-align: left;
    padding: var(--li-pad, 5px 8px); border-radius: 6px; background: transparent; color: var(--fg);
    font-size: 12.5px; line-height: 1.25;
  }
  .list-item:hover:not(:disabled) { background: var(--hover); }
  .list-item .dot { color: var(--border); font-size: 8px; flex: 0 0 auto; }
  .list-item .dot.seen { color: var(--accent); }
  .list-item .t { flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .list-item .c { color: var(--meta); font-family: Consolas, monospace; font-size: 10px; flex: 0 0 auto; }

  .gen-path {
    width: 100%; text-align: left; margin: 4px 0 2px; padding: 8px 10px; border-radius: 8px;
    font-size: 12.5px; font-weight: 600; cursor: pointer; color: var(--accent);
    background: color-mix(in srgb, var(--accent) 12%, transparent);
    border: 1px solid color-mix(in srgb, var(--accent) 32%, transparent);
  }
  .gen-path:hover:not(:disabled) { background: color-mix(in srgb, var(--accent) 20%, transparent); }
  .gen-path:disabled { opacity: .5; cursor: default; }
  .path-dot { color: var(--accent); }
  .list-item.path .t { font-weight: 600; }
  .path-chip {
    display: flex; align-items: center; gap: 8px; margin: 2px 0 4px;
    padding: 7px 9px; border-radius: 8px;
    background: color-mix(in srgb, var(--accent) 12%, transparent);
    border: 1px solid color-mix(in srgb, var(--accent) 35%, transparent);
  }
  .path-chip.done {
    background: color-mix(in srgb, #3fb950 14%, transparent);
    border-color: color-mix(in srgb, #3fb950 40%, transparent);
  }
  .pc-ico { color: var(--accent); font-size: 13px; flex: 0 0 auto; }
  .path-chip.done .pc-ico { color: #3fb950; }
  .pc-body { display: flex; flex-direction: column; min-width: 0; }
  .pc-title { font-size: 12.5px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .pc-step { font-size: 10.5px; color: var(--meta); font-variant-numeric: tabular-nums; }

  .user-bar {
    display: flex; align-items: center; gap: 8px; padding: 8px 10px;
    border-top: 1px solid var(--border); min-height: 46px;
  }
  .ub-left { display: flex; align-items: center; gap: 8px; flex: 1 1 auto; min-width: 0; padding: 5px 6px; border-radius: 8px; }
  .ub-spacer { flex: 1 1 auto; }
  .ub-left:hover { background: color-mix(in srgb, var(--fg) 6%, transparent); }
  .ub-dot { color: var(--accent); font-size: 14px; }
  .ub-name { font-size: 12.5px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .cost { font-size: 11px; color: var(--meta); font-variant-numeric: tabular-nums; }
  .gear { background: none; color: var(--meta); font-size: 16px; padding: 5px 7px; border-radius: 8px; flex: 0 0 auto; }
  .gear:hover { background: var(--hover); color: var(--fg); transform: rotate(35deg); transition: transform .15s; }

  @media (max-width: 720px) {
    .sidebar { position: fixed; z-index: 80; top: 0; left: 0; box-shadow: 0 0 50px rgba(0, 0, 0, .5); }
  }
</style>
