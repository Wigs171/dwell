<script lang="ts">
  // Knowledge-base detail window: opens when a gallery card is clicked (before loading
  // the brain). Shows the cover, title, full description, stats, and a Sources tab; the
  // "Open" button actually loads it (or resumes a warm session).
  import { dwell } from './dwell.svelte';
  import { api } from './api';

  let tab = $state<'about' | 'sources' | 'pending'>('about');
  let confirming = $state(false);
  const v = $derived(dwell.vaultDetail);
  const resumable = $derived(!!v && !!dwell.vaultStash[v.path]);
  // learned (+ pre-registry legacy) vs still-to-learn — the vault's saved learn queue
  const learnedSrc = $derived(dwell.vaultDetailSources.filter((s) => s.status !== 'pending'));
  const pendingSrc = $derived(dwell.vaultDetailSources.filter((s) => s.status === 'pending'));
  const srcCount = $derived(learnedSrc.length);
  const pendCount = $derived(pendingSrc.length);

  // Peek — read a source's text right here to refresh yourself on what it is.
  let peekKey = $state<string | null>(null);
  let peekText = $state('');
  let peekLoading = $state(false);
  async function togglePeek(s: { kind: string; name: string }) {
    const key = s.kind + '/' + s.name;
    if (peekKey === key) { peekKey = null; return; }
    peekKey = key; peekLoading = true; peekText = '';
    try {
      const p = await api.sourcePeek(v!.path, s.kind, s.name);
      peekText = p.text || p.note || '(empty)';
      if (p.truncated) peekText += '\n\n… (truncated)';
    } catch { peekText = '(could not read this source)'; }
    peekLoading = false;
  }
  // imported (external) vaults are FORGOTTEN (files kept); managed ones are DELETED.
  const purge = $derived(!v?.imported);
  const removeLabel = $derived(v?.imported ? 'Remove from library' : 'Delete');
  $effect(() => { void v?.path; confirming = false; });   // reset confirm when the modal changes

  let exporting = $state(false);
  let exportNote = $state('');
  async function exportOkf() {
    if (!v || exporting) return;
    exporting = true; exportNote = '';
    try {
      const r = await api.vaultExportOkf(v.path);
      exportNote = `exported ${r.concepts} concepts`;
      await dwell.refreshVaults();
    } catch { exportNote = 'export failed'; }
    exporting = false;
  }

  let coverInput = $state<HTMLInputElement>();
  function pickCover(e: Event) {
    const f = (e.currentTarget as HTMLInputElement).files?.[0];
    if (f) void dwell.setVaultCover(f);
    if (coverInput) coverInput.value = '';   // allow re-picking the same file
  }
</script>

{#if v}
  <div class="backdrop">
    <!-- a real button as the click-outside-to-close target (accessible; no div handler) -->
    <button class="bd-close" aria-label="Close details" onclick={() => dwell.closeVaultDetail()}></button>
    <div class="win" role="dialog" aria-label="Knowledge base details" aria-modal="true">
      <div class="hero">
        {#if v.has_cover}
          <img class="cover" src={`${api.vaultCoverUrl(v.path)}&v=${dwell.coverVersion}`} alt=""
               onerror={(e) => ((e.currentTarget as HTMLImageElement).style.display = 'none')} />
        {/if}
        <div class="scrim"></div>
        <button class="x" onclick={() => dwell.closeVaultDetail()} title="close">✕</button>
        <div class="herotext">
          <div class="title">{v.name}{v.has_voice ? ' 🎙' : ''}</div>
          <div class="stats">{v.nodes} pages{v.sources ? ` · ${v.sources} source${v.sources === 1 ? '' : 's'}` : ''}{resumable ? ' · ● warm session' : ''}</div>
        </div>
        <div class="coverctl">
          <button class="cc" onclick={() => coverInput?.click()}>{v.has_cover ? 'Change cover' : 'Add cover'}</button>
          {#if v.has_cover}<button class="cc rm" onclick={() => dwell.removeVaultCover()}>Remove</button>{/if}
          <input bind:this={coverInput} class="hidden" type="file" accept="image/png,image/jpeg,image/webp,image/gif" onchange={pickCover} />
        </div>
      </div>

      <div class="tabs">
        <button class="tab" class:active={tab === 'about'} onclick={() => (tab = 'about')}>About</button>
        <button class="tab" class:active={tab === 'sources'} onclick={() => (tab = 'sources')}>Sources{srcCount ? ` (${srcCount})` : ''}</button>
        <button class="tab" class:active={tab === 'pending'} onclick={() => (tab = 'pending')}>Pending{pendCount ? ` (${pendCount})` : ''}</button>
      </div>

      <div class="body">
        {#if tab === 'about'}
          {#if v.topic}<p class="desc">{v.topic}</p>{:else}<p class="desc dim">No description.</p>{/if}
        {:else}
          {@const rows = tab === 'sources' ? learnedSrc : pendingSrc}
          {#if dwell.vaultDetailSourcesLoading}
            <p class="dim">Loading sources…</p>
          {:else if !rows.length}
            <p class="dim">{tab === 'sources'
              ? 'No source documents listed for this knowledge base.'
              : 'Nothing waiting to be learned — add material via Expand.'}</p>
          {:else}
            <ul class="srclist">
              {#each rows as s (s.kind + '/' + s.name)}
                <li>
                  {#if s.kind === 'link'}
                    <a class="sname slink" href={s.name} target="_blank" rel="noreferrer" title="open the link">{s.name}</a>
                  {:else if s.kind === 'research'}
                    <span class="sname">{s.name}</span>
                  {:else}
                    <button class="sname speek" title="view this source" onclick={() => togglePeek(s)}>{s.name}</button>
                  {/if}
                  <span class="skind">{s.kind}{s.exts.length ? ` · ${s.exts.join(', ')}` : ''}</span>
                </li>
                {#if peekKey === s.kind + '/' + s.name}
                  <li class="peekrow">{#if peekLoading}<span class="dim">loading…</span>{:else}<pre class="peek">{peekText}</pre>{/if}</li>
                {/if}
              {/each}
            </ul>
            {#if tab === 'pending'}
              <p class="pendnote">These build when you Expand this knowledge base — choose which in the Learn queue.</p>
            {/if}
          {/if}
        {/if}
      </div>

      <div class="footer">
        <span class="del-zone">
          {#if confirming}
            <span class="confirm">{purge ? 'Delete permanently?' : 'Remove?'}</span>
            <button class="danger" onclick={() => dwell.removeVault(v, purge)}>Yes</button>
            <button class="no" onclick={() => (confirming = false)}>No</button>
          {:else}
            <button class="del" onclick={() => (confirming = true)}>{removeLabel}</button>
          {/if}
        </span>
        <span class="spacer"></span>
        <button class="ghost" disabled={exporting} onclick={exportOkf} title="export as an Open Knowledge Format bundle (a sibling '-okf' folder)">{exporting ? 'Exporting…' : exportNote || 'OKF'}</button>
        <button class="ghost" disabled={dwell.learnBusy} onclick={() => v && dwell.expandVault(v)} title="add more material to this knowledge base">Expand</button>
        <button class="open" disabled={dwell.loading} onclick={() => dwell.enterVaultDetail()}>
          {resumable ? '▸ Resume' : 'Open knowledge base'}
        </button>
      </div>
    </div>
  </div>
{/if}

<style>
  .backdrop {
    position: fixed; inset: 0; z-index: 95; display: flex; align-items: center; justify-content: center;
    background: rgba(0, 0, 0, .5); padding: 24px;
  }
  .bd-close { position: absolute; inset: 0; width: 100%; height: 100%; background: transparent; border: none; padding: 0; cursor: default; }
  .win {
    position: relative; z-index: 1;
    width: min(390px, 92vw); max-height: 90vh; display: flex; flex-direction: column;
    background: var(--bg); border: 1px solid var(--border); border-radius: 14px;
    box-shadow: 0 20px 70px #000b; overflow: hidden;
  }

  /* Hero matches the gallery card's 4/5 portrait so the cover frames identically — it
     reads as the card itself, grown to reveal the tabs + sources below. Capped so it
     never crowds out the content on shorter screens. */
  .hero {
    position: relative; aspect-ratio: 4 / 5; max-height: 48vh; flex-shrink: 0; color: #fff;
    background:
      radial-gradient(120% 80% at 70% 0%, color-mix(in srgb, var(--accent) 55%, transparent) 0%, transparent 60%),
      linear-gradient(155deg, color-mix(in srgb, var(--accent) 38%, var(--panel)) 0%, var(--panel) 78%);
  }
  .cover { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; }
  .scrim { position: absolute; inset: 0; background: linear-gradient(to top, rgba(0,0,0,.82) 0%, rgba(0,0,0,.25) 55%, rgba(0,0,0,.1) 100%); }
  .x {
    position: absolute; top: 10px; right: 10px; z-index: 1;
    width: 28px; height: 28px; border-radius: 50%; padding: 0;
    background: rgba(0,0,0,.4); color: #fff; font-size: 13px; backdrop-filter: blur(4px);
  }
  .x:hover { background: rgba(0,0,0,.65); }
  .herotext { position: absolute; left: 0; right: 0; bottom: 0; padding: 16px 18px; text-shadow: 0 1px 4px rgba(0,0,0,.85); }
  .coverctl { position: absolute; top: 10px; left: 10px; z-index: 2; display: flex; gap: 6px; }
  .cc { background: rgba(0,0,0,.45); color: #fff; border: 1px solid rgba(255,255,255,.28); font-size: 11.5px; padding: 5px 10px; border-radius: 7px; cursor: pointer; -webkit-backdrop-filter: blur(4px); backdrop-filter: blur(4px); }
  .cc:hover { background: rgba(0,0,0,.72); }
  .cc.rm:hover { background: var(--err); border-color: var(--err); }
  .hidden { display: none; }
  .title { font-size: 20px; font-weight: 700; line-height: 1.2; }
  .stats { font-size: 12px; opacity: .85; margin-top: 5px; font-variant-numeric: tabular-nums; }

  .tabs { display: flex; gap: 0; border-bottom: 1px solid var(--border); padding: 0 8px; background: var(--panel); flex-shrink: 0; }
  .tab { background: none; border: none; border-bottom: 2px solid transparent; color: var(--meta); font-size: 12.5px; padding: 9px 14px; border-radius: 0; }
  .tab:hover { color: var(--fg); background: transparent; }
  .tab.active { color: var(--accent); border-bottom-color: var(--accent); }

  .body { padding: 16px 18px; overflow-y: auto; flex: 1 1 auto; }
  .desc { margin: 0; font-size: 14px; line-height: 1.6; color: var(--fg); }
  .dim { color: var(--meta); }
  .srclist { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 2px; }
  .srclist li {
    display: flex; align-items: baseline; justify-content: space-between; gap: 12px;
    padding: 7px 9px; border-radius: 7px;
  }
  .srclist li:hover { background: color-mix(in srgb, var(--accent) 8%, transparent); }
  .sname { font-size: 13px; color: var(--fg); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .skind { font-size: 11px; color: var(--meta); flex-shrink: 0; font-variant-numeric: tabular-nums; }
  button.speek, a.slink { background: none; border: none; padding: 0; text-align: left; text-decoration: none; cursor: pointer; font-family: inherit; }
  button.speek:hover, a.slink:hover { color: var(--accent); text-decoration: underline; }
  .peekrow { display: block !important; padding: 0 9px 8px; }
  .peekrow:hover { background: none !important; }
  .peek {
    margin: 2px 0 0; max-height: 220px; overflow-y: auto; white-space: pre-wrap;
    font-size: 11.5px; line-height: 1.55; color: var(--fg); font-family: inherit;
    background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px;
  }
  .pendnote { font-size: 11.5px; color: var(--meta); margin: 10px 2px 0; }

  .footer { display: flex; align-items: center; gap: 9px; padding: 12px 16px; border-top: 1px solid var(--border); background: var(--panel); flex-shrink: 0; }
  .footer .spacer { flex: 1 1 auto; }
  .del-zone { display: flex; align-items: center; gap: 8px; }
  .del { background: none; border: none; color: var(--meta); font-size: 12.5px; padding: 6px 4px; cursor: pointer; }
  .del:hover { color: var(--err); }
  .confirm { font-size: 12px; color: var(--fg); }
  .danger { background: var(--err); border: 1px solid var(--err); color: #fff; font-size: 12.5px; font-weight: 600; padding: 6px 12px; border-radius: 7px; cursor: pointer; }
  .no { background: none; border: 1px solid var(--border); color: var(--meta); font-size: 12.5px; padding: 6px 11px; border-radius: 7px; cursor: pointer; }
  .no:hover { color: var(--fg); }
  .ghost { background: none; border: 1px solid var(--border); color: var(--fg); font-size: 13px; padding: 8px 14px; border-radius: 8px; }
  .ghost:hover { background: var(--hover); }
  .open { background: var(--accent); border: 1px solid var(--accent); color: var(--bg); font-weight: 600; font-size: 13px; padding: 8px 16px; border-radius: 8px; }
  .open:hover { filter: brightness(1.08); }
  .open:disabled { opacity: .5; }
</style>
