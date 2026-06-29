<script lang="ts">
  // The vault picker: a gallery of full-bleed HERO cards (one per vault). This is
  // the only way to pick/switch a vault (the sidebar dropdown was retired). Each
  // card shows the vault's explicit cover image (else a themed gradient), its title,
  // a one-line blurb, and stats. Clicking a card loads that vault's brain.
  import { dwell } from './dwell.svelte';
  import { api } from './api';

  const stats = (v: { nodes: number; sources?: number; has_voice: boolean }) => {
    const bits = [`${v.nodes} pages`];
    if (v.sources) bits.push(`${v.sources} source${v.sources === 1 ? '' : 's'}`);
    return bits.join(' · ');
  };

  let importOpen = $state(false);
  let importPath = $state('');
  let importing = $state(false);
  async function doImport() {
    importing = true;
    const ok = await dwell.importVault(importPath);
    importing = false;
    if (ok) { importPath = ''; importOpen = false; }
  }
</script>

<div class="gallery">
  <header class="ghead">
    <h1>Choose a knowledge base</h1>
    <span class="root" title={dwell.vaultRoot}>{dwell.vaults.length} vault{dwell.vaults.length === 1 ? '' : 's'}</span>
    <span class="spacer"></span>
    <button class="import-btn" onclick={() => (importOpen = !importOpen)}>{importOpen ? 'Cancel' : 'Add existing'}</button>
  </header>

  {#if importOpen}
    <div class="import-row">
      <input type="text" bind:value={importPath} placeholder="Paste the full path to an existing knowledge-base folder"
             onkeydown={(e) => { if (e.key === 'Enter') doImport(); }} />
      <button class="go" disabled={importPath.trim().length < 2 || importing} onclick={doImport}>{importing ? 'Adding…' : 'Add'}</button>
    </div>
  {/if}

  {#if !dwell.vaults.length}
    <div class="empty">No vaults found under {dwell.vaultRoot || 'the vault root'}.</div>
  {:else}
    <div class="grid">
      {#each dwell.vaults as v (v.path)}
        {@const loadingThis = dwell.loading && dwell.vaultPath === v.path}
        {@const resumable = !!dwell.vaultStash[v.path]}
        <button class="card" class:loading={loadingThis} disabled={dwell.loading}
                title={resumable ? 'Resume where you left off' : (v.topic || v.name)}
                onclick={() => dwell.openVaultDetail(v)}>
          {#if v.has_cover}
            <img class="cover" src={`${api.vaultCoverUrl(v.path)}&v=${dwell.coverVersion}`} alt="" loading="lazy"
                 onerror={(e) => ((e.currentTarget as HTMLImageElement).style.display = 'none')} />
          {/if}
          {#if resumable}<div class="resume">● Resume</div>{/if}
          <div class="scrim"></div>
          <div class="body">
            <div class="title">{v.name}{v.has_voice ? ' 🎙' : ''}</div>
            {#if v.topic}<div class="blurb" title={v.topic}>{v.topic}</div>{/if}
            <div class="stats">{stats(v)}</div>
          </div>
          {#if loadingThis}<div class="spin">Loading…</div>{/if}
        </button>
      {/each}
    </div>
  {/if}
</div>

<style>
  .gallery { flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: 26px 30px 40px; }
  .ghead { display: flex; align-items: baseline; gap: 12px; margin: 4px 2px 20px; }
  .ghead h1 { font-size: 22px; font-weight: 650; margin: 0; color: var(--fg); letter-spacing: .2px; }
  .root { font-size: 12px; color: var(--meta); }
  .ghead .spacer { flex: 1 1 auto; }
  .import-btn { background: none; border: 1px solid var(--border); color: var(--meta); font-size: 12.5px; padding: 6px 12px; border-radius: 8px; cursor: pointer; }
  .import-btn:hover { color: var(--fg); border-color: var(--accent); }
  .import-row { display: flex; gap: 8px; margin: 0 2px 18px; }
  .import-row input {
    flex: 1; box-sizing: border-box; padding: 9px 11px; font-size: 13px;
    background: var(--panel); color: var(--fg); border: 1px solid var(--border); border-radius: 8px;
  }
  .import-row input:focus { outline: none; border-color: var(--accent); }
  .import-row .go { background: var(--accent); border: 1px solid var(--accent); color: var(--bg); font-weight: 600; font-size: 13px; padding: 8px 16px; border-radius: 8px; cursor: pointer; }
  .import-row .go:disabled { opacity: .45; cursor: default; }
  .empty { color: var(--meta); font-style: italic; padding: 20px 2px; }

  .grid {
    display: grid; gap: 20px;
    grid-template-columns: repeat(auto-fill, minmax(264px, 1fr));
  }

  /* Hero card — full-bleed cover, vault title + blurb in a smooth bottom scrim,
     white drop-shadowed text legible over any image (mirrors the #layouts hero). */
  .card {
    position: relative; overflow: hidden; aspect-ratio: 4 / 5;
    border-radius: 14px; border: 1px solid var(--border); padding: 0;
    cursor: pointer; text-align: left; color: #fff;
    /* themed gradient base — shows through for coverless vaults */
    background:
      radial-gradient(120% 80% at 70% 0%, color-mix(in srgb, var(--accent) 55%, transparent) 0%, transparent 60%),
      linear-gradient(155deg, color-mix(in srgb, var(--accent) 38%, var(--panel)) 0%, var(--panel) 78%);
    box-shadow: 0 2px 10px rgba(0, 0, 0, .18);
    transition: transform .16s ease, box-shadow .16s ease, border-color .16s ease;
  }
  .card:hover:not(:disabled) {
    transform: translateY(-3px);
    box-shadow: 0 10px 26px rgba(0, 0, 0, .34);
    border-color: color-mix(in srgb, var(--accent) 60%, var(--border));
  }
  .card:disabled { cursor: default; }
  .card:not(.loading):disabled { opacity: .5; }

  .cover { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }

  /* a separate masked scrim layer: blurred gradient fades in gradually (no hard
     edge where the blur ends), keeping text legible over any image/theme. */
  .scrim {
    position: absolute; left: 0; right: 0; bottom: 0; height: 76%; pointer-events: none;
    background: linear-gradient(to top, rgba(0,0,0,.88) 0%, rgba(0,0,0,.5) 40%, rgba(0,0,0,0) 100%);
    -webkit-backdrop-filter: blur(3px); backdrop-filter: blur(3px);
    -webkit-mask-image: linear-gradient(to top, #000 44%, transparent 100%);
    mask-image: linear-gradient(to top, #000 44%, transparent 100%);
  }
  .body {
    position: absolute; left: 0; right: 0; bottom: 0; padding: 18px 18px 16px;
    text-shadow: 0 1px 3px rgba(0,0,0,.85), 0 2px 16px rgba(0,0,0,.5);
  }
  .title { font-size: 18px; font-weight: 650; line-height: 1.18; margin-bottom: 6px; }
  .blurb {
    font-size: 12.5px; line-height: 1.4; color: rgba(255,255,255,.9); margin-bottom: 8px;
    display: -webkit-box; -webkit-line-clamp: 3; line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden;
  }
  .stats { font-size: 11px; color: rgba(255,255,255,.78); font-variant-numeric: tabular-nums; }

  .spin {
    position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
    background: rgba(0,0,0,.32); font-size: 13px; color: #fff; letter-spacing: .3px;
  }
  /* warm-session marker → clicking re-enters instantly, exactly where you left off */
  .resume {
    position: absolute; top: 10px; left: 10px; z-index: 1;
    display: flex; align-items: center; gap: 5px;
    padding: 3px 9px; border-radius: 999px;
    font-size: 11px; font-weight: 500; letter-spacing: .2px;
    color: #fff; background: color-mix(in srgb, var(--accent) 82%, transparent);
    box-shadow: 0 1px 6px rgba(0,0,0,.4);
  }
</style>
