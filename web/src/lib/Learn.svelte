<script lang="ts">
  // Learn — build a knowledge base. Create a draft, add material (files / links),
  // curate the source list, then run the ingest swarm (see BuildPanel) to commit it
  // into a readable vault.
  import { dwell } from './dwell.svelte';
  import BuildPanel from './BuildPanel.svelte';

  // create form
  let name = $state('');
  let topic = $state('');
  // meta editor (seeded from the draft when it exists)
  let links = $state('');
  let fileInput = $state<HTMLInputElement>();
  let coverInput = $state<HTMLInputElement>();

  const src = $derived(dwell.learnSources);
  const draftVault = $derived(dwell.learnDraft?.vault ?? '');
  const linkList = $derived(links.split('\n').map((l) => l.trim()).filter(Boolean));
  const fmtSize = (n: number) => (n < 1024 * 1024 ? `${Math.round(n / 1024)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`);
  const hasSources = $derived(!!src && (src.files.length > 0 || src.links.length > 0));

  let expandPath = $state('');   // a chosen existing knowledge base to expand

  async function create() {
    await dwell.learnCreate(name, topic);
  }
  function doExpand() {
    const v = dwell.vaults.find((x) => x.path === expandPath);
    if (v) void dwell.expandVault(v);
  }
  function pickFiles(e: Event) {
    const fs = [...((e.currentTarget as HTMLInputElement).files ?? [])];
    if (fs.length) void dwell.learnUpload(fs);
    if (fileInput) fileInput.value = '';   // allow re-picking the same file
  }
  function saveMeta() {
    void dwell.learnSaveMeta('', linkList);
  }
  function pickCover(e: Event) {
    const f = (e.currentTarget as HTMLInputElement).files?.[0];
    if (f && dwell.learnDraft) void dwell.setVaultCover(f, dwell.learnDraft.vault);
    if (coverInput) coverInput.value = '';
  }
</script>

<div class="learn">
  <div class="inner">
    {#if !dwell.learnDraft}
      <!-- step 1: create a draft -->
      <header>
        <h1>Build a knowledge base</h1>
        <p class="sub">Name it, then add your material — files and links — and an agent swarm will ingest and weave it into a readable knowledge base.</p>
      </header>

      <div class="field">
        <label for="kbname">Name</label>
        <input id="kbname" type="text" bind:value={name} placeholder="e.g. Roman Aqueducts" />
      </div>
      <div class="field">
        <label for="kbtopic">What's it about? <span class="opt">optional</span></label>
        <input id="kbtopic" type="text" bind:value={topic} placeholder="A one-line description to focus the build" />
      </div>
      <div class="actions">
        <button class="primary" disabled={name.trim().length < 2 || dwell.learnBusy} onclick={create}>
          {dwell.learnBusy ? 'Creating…' : 'Create draft'}
        </button>
      </div>

      {#if dwell.vaults.length}
        <div class="orline"><span>or expand an existing one</span></div>
        <div class="field">
          <label for="expandkb">Expand a knowledge base</label>
          <div class="expandrow">
            <select id="expandkb" bind:value={expandPath}>
              <option value="" disabled selected>Choose a knowledge base…</option>
              {#each dwell.vaults as v (v.path)}<option value={v.path}>{v.name}</option>{/each}
            </select>
            <button class="add" disabled={!expandPath || dwell.learnBusy} onclick={doExpand}>Expand</button>
          </div>
          <p class="hint">Add new material on top of a built knowledge base, then re-ingest to grow it.</p>
        </div>
      {/if}
    {:else if dwell.buildActive}
      <BuildPanel />
    {:else}
      <!-- step 2: add material + curate -->
      <header>
        <div class="titlerow">
          <h1>{dwell.learnMode === 'expand' ? 'Expand · ' : ''}{dwell.learnDraft.name}</h1>
          <button class="discard" onclick={() => { const ex = dwell.learnMode === 'expand'; dwell.learnDiscard(); if (ex) dwell.page = 'read'; }} title="leave (your sources stay saved)">Close</button>
        </div>
        <p class="sub">{dwell.learnMode === 'expand'
          ? 'Add new material on top of this knowledge base. When you’re ready, re-ingest to grow it.'
          : 'Add material and curate the sources. When you’re ready, build it into a readable knowledge base.'}</p>
      </header>

      <div class="field">
        <div class="flabel">Cover image <span class="opt">optional</span></div>
        <div class="coverrow">
          {#if dwell.learnHasCover}
            <img class="coverthumb" src={`/vault-cover?vault=${encodeURIComponent(draftVault)}&v=${dwell.coverVersion}`} alt="cover" />
          {/if}
          <button class="add" onclick={() => coverInput?.click()}>{dwell.learnHasCover ? 'Change cover' : 'Add cover'}</button>
          {#if dwell.learnHasCover}<button class="add" onclick={() => dwell.removeVaultCover(draftVault)}>Remove</button>{/if}
          <input bind:this={coverInput} class="hidden" type="file" accept="image/png,image/jpeg,image/webp,image/gif" onchange={pickCover} />
        </div>
      </div>

      <div class="field">
        <div class="flabel">Add files <span class="opt">PDFs, Markdown, text</span></div>
        <button class="add" disabled={dwell.learnBusy} onclick={() => fileInput?.click()}>
          {dwell.learnBusy ? 'Uploading…' : '+ Add files'}
        </button>
        <input bind:this={fileInput} class="hidden" type="file" multiple accept=".pdf,.md,.markdown,.txt" onchange={pickFiles} />
      </div>

      <div class="field">
        <label for="kblinks">Links <span class="opt">videos or web pages, one per line</span></label>
        <textarea id="kblinks" rows="3" bind:value={links} placeholder="https://youtube.com/watch?v=…&#10;https://en.wikipedia.org/wiki/…"></textarea>
        <div><button class="add" onclick={saveMeta}>Save links</button></div>
      </div>

      {#if src}
        <div class="sources">
          <div class="sec">Sources{src.files.length + src.links.length ? ` · ${src.files.length + src.links.length}` : ''}</div>
          {#if !src.files.length && !src.links.length}
            <p class="empty">No sources yet. Add files or links above.</p>
          {:else}
            <ul>
              {#each src.files as f (f.id)}
                <li class:dup={f.status === 'duplicate'}><span class="badge">file</span><span class="sn">{f.name}.{f.ext}</span>
                  {#if f.status === 'duplicate'}<span class="dupbadge" title="identical to a source already ingested — it will be skipped at build">already ingested</span>{/if}
                  <span class="meta">{fmtSize(f.size)}</span>
                  <button class="rm" title="remove" onclick={() => dwell.learnRemoveSource(f.id)}>✕</button></li>
              {/each}
              {#each src.links as l (l.id)}
                <li><span class="badge link">link</span><span class="sn">{l.name}</span>
                  <button class="rm" title="remove" onclick={() => dwell.learnRemoveSource(l.id)}>✕</button></li>
              {/each}
            </ul>
          {/if}
        </div>
      {/if}

      <div class="actions">
        <button class="primary" disabled={!hasSources} onclick={() => dwell.startBuild()}>
          {dwell.learnMode === 'expand' ? 'Re-ingest & expand' : 'Build knowledge base'}
        </button>
        <span class="note">Runs the ingest swarm over your sources — you'll see live progress and can stop it.</span>
      </div>
    {/if}
  </div>
</div>

<style>
  .learn { flex: 1 1 auto; overflow-y: auto; padding: 28px 30px 48px; }
  .inner { max-width: 620px; margin: 0 auto; }
  header { margin-bottom: 22px; }
  .titlerow { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
  h1 { font-size: 24px; font-weight: 700; margin: 0 0 6px; color: var(--fg); }
  .sub { font-size: 13.5px; line-height: 1.6; color: var(--meta); margin: 0; }

  .field { margin-bottom: 18px; display: flex; flex-direction: column; gap: 7px; }
  label, .flabel { font-size: 13px; font-weight: 600; color: var(--fg); }
  .opt { font-weight: 400; color: var(--meta); font-size: 11.5px; }
  input[type="text"], textarea {
    width: 100%; box-sizing: border-box; padding: 9px 11px; font-size: 13.5px;
    background: var(--panel); color: var(--fg);
    border: 1px solid var(--border); border-radius: 9px; font-family: inherit; resize: vertical;
  }
  input[type="text"]:focus, textarea:focus { outline: none; border-color: var(--accent); }
  .hidden { display: none; }
  .add {
    align-self: flex-start; background: var(--panel); color: var(--fg); border: 1px solid var(--border);
    border-radius: 8px; padding: 7px 13px; font-size: 13px; cursor: pointer; margin-top: 2px;
  }
  .add:hover:not(:disabled) { border-color: var(--accent); }
  .add:disabled { opacity: .5; }
  .orline { display: flex; align-items: center; gap: 12px; margin: 24px 0 8px; color: var(--meta); font-size: 11.5px; text-transform: uppercase; letter-spacing: .1em; }
  .orline::before, .orline::after { content: ''; flex: 1; height: 1px; background: var(--border); }
  .expandrow { display: flex; gap: 8px; align-items: stretch; }
  .expandrow select { flex: 1; min-width: 0; padding: 9px 11px; font-size: 13.5px; background: var(--panel); color: var(--fg); border: 1px solid var(--border); border-radius: 9px; font-family: inherit; }
  .expandrow select:focus { outline: none; border-color: var(--accent); }
  .expandrow .add { margin-top: 0; }
  .hint { font-size: 11.5px; color: var(--meta); margin: 6px 0 0; line-height: 1.5; }
  .coverrow { display: flex; align-items: center; gap: 10px; }
  .coverthumb { width: 64px; height: 80px; object-fit: cover; border-radius: 8px; border: 1px solid var(--border); }

  .sources { margin: 6px 0 8px; }
  .sec { font-size: 11px; text-transform: uppercase; letter-spacing: .12em; color: var(--meta); margin-bottom: 8px; }
  .empty { color: var(--meta); font-size: 13px; font-style: italic; margin: 0; }
  .sources ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
  .sources li { display: flex; align-items: center; gap: 9px; padding: 7px 10px; background: var(--panel); border: 1px solid var(--border); border-radius: 8px; }
  .badge { font-size: 10px; text-transform: uppercase; letter-spacing: .08em; color: var(--bg); background: var(--accent); border-radius: 5px; padding: 2px 6px; flex-shrink: 0; }
  .sources li.dup { opacity: .62; }
  .dupbadge { font-size: 10px; text-transform: uppercase; letter-spacing: .06em; color: var(--meta); border: 1px solid var(--border); border-radius: 5px; padding: 2px 6px; flex-shrink: 0; }
  .badge.link { background: color-mix(in srgb, var(--accent) 55%, var(--fg)); }
  .sn { flex: 1; font-size: 12.5px; color: var(--fg); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .meta { font-size: 11px; color: var(--meta); flex-shrink: 0; font-variant-numeric: tabular-nums; }
  .rm { background: none; border: none; color: var(--meta); cursor: pointer; padding: 0 2px; font-size: 12px; flex-shrink: 0; }
  .rm:hover { color: var(--err); }

  .actions { display: flex; align-items: center; gap: 12px; margin-top: 24px; flex-wrap: wrap; }
  .primary { background: var(--accent); border: 1px solid var(--accent); color: var(--bg); font-weight: 650; font-size: 14px; padding: 10px 20px; border-radius: 10px; cursor: pointer; }
  .primary:hover:not(:disabled) { filter: brightness(1.08); }
  .primary:disabled { opacity: .45; cursor: default; }
  .discard { background: none; border: 1px solid var(--border); color: var(--meta); font-size: 12px; padding: 5px 11px; border-radius: 7px; cursor: pointer; }
  .discard:hover { color: var(--fg); border-color: var(--accent); }
  .note { font-size: 11.5px; color: var(--meta); }
</style>
