<script lang="ts">
  import { dwell, QUIZ_TYPE_KEYS, READING_LEVELS, OUTPUT_FORMS, LANGUAGES } from './dwell.svelte';
  import { THEMES, BG_PATTERNS, themeByName, type Theme } from './themes';

  const QUIZ_TYPE_LABELS: Record<string, string> = {
    choice: 'Multiple choice', truefalse: 'True / false', cloze: 'Fill in the blank',
    recall: 'Free recall', matching: 'Matching',
  };

  let win = $state<HTMLDivElement>();
  let x = $state<number | null>(null);
  let y = $state<number | null>(null);
  let drag: { dx: number; dy: number } | null = null;
  let tab = $state<'customize' | 'read' | 'learn'>('read');
  let advancedModel = $state(false);   // disclosure for the mechanical-tier model

  // add-endpoint form (Models & Keys)
  let addingEp = $state(false);
  let epName = $state('');
  let epUrl = $state('');
  let epKey = $state('');
  let epErr = $state('');
  // Quick-add presets — fill the base URL so the user only pastes a key.
  // Base URLs copied from Odysseus's provider list (all OpenAI-compatible bar Anthropic).
  const EP_PRESETS = [
    { name: 'OpenAI', url: 'https://api.openai.com/v1' },
    { name: 'OpenRouter', url: 'https://openrouter.ai/api/v1' },
    { name: 'Anthropic', url: 'https://api.anthropic.com' },
    { name: 'Google Gemini', url: 'https://generativelanguage.googleapis.com/v1beta/openai' },
    { name: 'DeepSeek', url: 'https://api.deepseek.com/v1' },
    { name: 'Groq', url: 'https://api.groq.com/openai/v1' },
    { name: 'Mistral', url: 'https://api.mistral.ai/v1' },
    { name: 'xAI Grok', url: 'https://api.x.ai/v1' },
    { name: 'Together AI', url: 'https://api.together.xyz/v1' },
    { name: 'Fireworks AI', url: 'https://api.fireworks.ai/inference/v1' },
    { name: 'Z.AI (Zhipu)', url: 'https://api.z.ai/api/paas/v4' },
    { name: 'Ollama', url: 'http://localhost:11434/v1' },
  ];
  function applyPreset(p: { name: string; url: string }) {
    epName = p.name; epUrl = p.url; epErr = '';
  }
  async function saveEndpoint() {
    epErr = '';
    try {
      await dwell.addEndpoint(epName.trim(), epUrl.trim(), epKey.trim());
      addingEp = false; epName = ''; epUrl = ''; epKey = '';
    } catch (e) {
      epErr = e instanceof Error ? e.message : String(e);
    }
  }

  // web search provider (research prompts)
  let searchProv = $state('tavily');
  let searchKey = $state('');
  $effect(() => { searchProv = dwell.searchProvider || 'tavily'; });
  async function saveSearch() {
    if (!searchKey.trim()) return;
    try { await dwell.setSearchKey(searchProv, searchKey.trim()); searchKey = ''; } catch { /* surfaced in status */ }
  }

  const COLS: { key: 'bg' | 'panel' | 'fg' | 'border' | 'accent'; label: string }[] = [
    { key: 'bg', label: 'Background' }, { key: 'fg', label: 'Text' },
    { key: 'panel', label: 'Panel / page' }, { key: 'border', label: 'Border' },
    { key: 'accent', label: 'Accent' },
  ];
  const swatchDots = (t: Theme) => [t.bg, t.panel, t.fg, t.accent];

  // Learn model selection: when an endpoint is chosen, the role pickers list ITS
  // models; otherwise the curated Claude list (Anthropic / .env default).
  const learnEp = $derived(dwell.selectedLearnEndpoint);
  const epModels = $derived(learnEp ? learnEp.models : null);
  const epLabel = $derived(learnEp ? `${learnEp.provider} · ${learnEp.name}` : 'no provider');
  const learnEnabledEndpoints = $derived(dwell.endpoints.filter((e) => e.enabled));

  // Mercury (reading engine) key — Settings → Read
  let mkInput = $state('');
  let editingMercury = $state(false);
  async function saveMercury() {
    if (!mkInput.trim()) return;
    await dwell.setMercuryKey(mkInput.trim());
    mkInput = ''; editingMercury = false;
  }

  const active = $derived(themeByName(dwell.theme, dwell.customThemes));
  const staticBg = $derived(dwell.bgPattern === 'none' || dwell.bgPattern === 'dots');
  const fgColor = $derived.by(() => { void dwell.theme; return getComputedStyle(document.documentElement).getPropertyValue('--fg').trim() || '#cccccc'; });

  // Customize draft — reseeded whenever the active theme changes (a deliberate
  // swap), but untouched while you edit (editing only previews, never sets theme).
  let draft = $state<Theme>({ name: '', bg: '', fg: '', panel: '', border: '', accent: '' });
  $effect(() => {
    const a = themeByName(dwell.theme, dwell.customThemes);
    draft = { name: a.custom ? a.name : `${a.name}-custom`, bg: a.bg, fg: a.fg, panel: a.panel, border: a.border, accent: a.accent };
  });
  function setColor(k: keyof Theme, v: string) { draft = { ...draft, [k]: v }; dwell.previewTheme(draft); }
  function resetColor(k: 'bg' | 'panel' | 'fg' | 'border' | 'accent') { setColor(k, active[k]); }

  function down(e: MouseEvent) {
    const r = win!.getBoundingClientRect();
    drag = { dx: e.clientX - r.left, dy: e.clientY - r.top }; x = r.left; y = r.top;
    window.addEventListener('mousemove', move); window.addEventListener('mouseup', up);
  }
  function move(e: MouseEvent) { if (!drag) return; x = Math.max(0, Math.min(window.innerWidth - 90, e.clientX - drag.dx)); y = Math.max(0, Math.min(window.innerHeight - 36, e.clientY - drag.dy)); }
  function up() { drag = null; window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); }
</script>

<div class="overlay">
  <div class="win" bind:this={win} role="dialog" aria-label="Settings"
       style={x !== null ? `left:${x}px; top:${y}px; transform:none;` : ''}>
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="titlebar" onmousedown={down}>
      <span class="title">⚙ Settings</span>
      <button class="x" onclick={() => dwell.closeSettings()} title="close">✕</button>
    </div>

    <div class="tabs">
      <button class="tab" class:active={tab === 'read'} onclick={() => (tab = 'read')}>◈ Read</button>
      <button class="tab" class:active={tab === 'learn'} onclick={() => (tab = 'learn')}>✦ Learn</button>
      <button class="tab" class:active={tab === 'customize'} onclick={() => (tab = 'customize')}>✎ Customize</button>
    </div>

    <div class="body">
      {#if tab === 'customize'}
        <div class="card">
          <h2>Theme presets</h2>
          <div class="grid">
            {#each THEMES as t (t.name)}
              <button class="swatch" class:active={dwell.theme === t.name} onclick={() => dwell.setTheme(t.name)} title={t.name}>
                <span class="dots">{#each swatchDots(t) as c}<span style="background:{c}"></span>{/each}</span>
                <span class="sw-name">{t.name}</span>
              </button>
            {/each}
          </div>
        </div>
        {#if dwell.customThemes.length}
          <div class="card">
            <h2>Your themes</h2>
            <div class="grid">
              {#each dwell.customThemes as t (t.name)}
                <div class="swatch-wrap">
                  <button class="swatch" class:active={dwell.theme === t.name} onclick={() => dwell.setTheme(t.name)} title={t.name}>
                    <span class="dots">{#each swatchDots(t) as c}<span style="background:{c}"></span>{/each}</span>
                    <span class="sw-name">{t.name}</span>
                  </button>
                  <button class="del" title="delete theme" onclick={() => dwell.deleteCustomTheme(t.name)}>✕</button>
                </div>
              {/each}
            </div>
          </div>
        {/if}

        <div class="card">
          <h2>Colors</h2>
          {#each COLS as f (f.key)}
            <div class="color-row">
              <label for="cc-{f.key}">{f.label}</label>
              <input id="cc-{f.key}" type="color" value={draft[f.key]} oninput={(e) => setColor(f.key, e.currentTarget.value)} />
              <button class="reset" class:changed={draft[f.key] !== active[f.key]} title="reset to theme" onclick={() => resetColor(f.key)}>↺</button>
            </div>
          {/each}
          <div class="save-row">
            <input class="name" type="text" bind:value={draft.name} placeholder="theme name" />
            <button class="primary" onclick={() => dwell.saveCustomTheme(draft)} disabled={!draft.name.trim()}>Save theme</button>
          </div>
        </div>

        <div class="card">
          <h2>Background</h2>
          <div class="color-row"><label for="bgp">Pattern</label>
            <select id="bgp" value={dwell.bgPattern} onchange={(e) => dwell.setBgPattern(e.currentTarget.value)}>
              {#each BG_PATTERNS as p}<option value={p}>{p}</option>{/each}
            </select></div>
          <div class="color-row" class:off={staticBg}><label for="bgi">Intensity</label>
            <input id="bgi" type="range" min="0" max="1" step="0.05" value={dwell.bgIntensity} disabled={staticBg} oninput={(e) => dwell.setBgIntensity(+e.currentTarget.value)} /></div>
          <div class="color-row" class:off={staticBg}><label for="bgs">Size</label>
            <input id="bgs" type="range" min="0.3" max="2.5" step="0.1" value={dwell.bgSize} disabled={staticBg} oninput={(e) => dwell.setBgSize(+e.currentTarget.value)} /></div>
          <div class="color-row"><label for="bgc">Effect color</label>
            <input id="bgc" type="color" value={dwell.bgEffectColor || fgColor} oninput={(e) => dwell.setBgEffectColor(e.currentTarget.value)} />
            <button class="reset changed" title="use theme color" onclick={() => dwell.setBgEffectColor('')}>↺</button></div>
          <label class="chk"><input type="checkbox" checked={dwell.frosted} onchange={(e) => dwell.setFrosted(e.currentTarget.checked)} /> ❄ frosted glass</label>
        </div>

        <div class="card">
          <h2>Display</h2>
          <div class="color-row"><label for="den">Density</label>
            <select id="den" value={dwell.density} onchange={(e) => dwell.setDensity(e.currentTarget.value)}>
              <option value="compact">compact</option><option value="comfortable">comfortable</option><option value="spacious">spacious</option>
            </select></div>
        </div>
      {/if}

      {#if tab === 'read'}
        <div class="card">
          <h2>Reading</h2>
          <div class="color-row"><label for="vc">Voice</label>
            {#if dwell.voices}
              <select id="vc" value={dwell.voice} onchange={(e) => dwell.setVoice(e.currentTarget.value)}>
                {#if dwell.voices.vault_voices.length}<optgroup label="vault">{#each dwell.voices.vault_voices as v}<option value={v}>{v}</option>{/each}</optgroup>{/if}
                <optgroup label="presets">{#each dwell.voices.presets as v}<option value={v}>{v}</option>{/each}</optgroup>
              </select>
            {:else}<select id="vc" disabled><option>open a knowledge base to choose a voice…</option></select>{/if}</div>
          <div class="color-row"><label for="lvl">Level</label>
            <select id="lvl" value={dwell.level} onchange={(e) => dwell.setLevel(e.currentTarget.value)}>
              {#each READING_LEVELS as l (l.value)}<option value={l.value}>{l.label}</option>{/each}
            </select></div>
          <div class="color-row"><label for="frm">Form</label>
            <select id="frm" value={dwell.form} onchange={(e) => dwell.setForm(e.currentTarget.value)}>
              {#each OUTPUT_FORMS as f (f.value)}<option value={f.value}>{f.label}</option>{/each}
            </select></div>
          <div class="color-row"><label for="lng">Language</label>
            <select id="lng" value={dwell.language} onchange={(e) => dwell.setLanguage(e.currentTarget.value)}>
              {#each LANGUAGES as l (l.value)}<option value={l.value}>{l.label}</option>{/each}
            </select></div>
          <div class="color-row"><label for="wnd">Wander</label>
            <input id="wnd" type="range" min="0" max="1" step="0.05" value={dwell.wander} oninput={(e) => dwell.setWander(+e.currentTarget.value)} /></div>
          <label class="chk"><input type="checkbox" bind:checked={dwell.diffuse} /> ✦ diffuse (denoise-in)</label>
          <label class="chk"><input type="checkbox" bind:checked={dwell.dry} disabled={!!dwell.session} /> free (no-LLM) mode</label>
        </div>

        <div class="card">
          <h2>Reading model <span class="note">· Mercury</span></h2>
          <p class="hint">Dwell reads with <strong>Mercury</strong> — a text-diffusion model, the special engine that makes the streaming reader work. There's no alternative to swap in.</p>
          <div class="color-row"><label for="mk">Mercury key</label>
            {#if dwell.mercuryHasKey && !editingMercury}
              <span class="ep-models">set ✓</span>
              <button class="ep-act" title="change" onclick={() => { editingMercury = true; mkInput = ''; }}>change</button>
              <button class="ep-act rm" title="remove (fall back to .env)" onclick={() => dwell.clearMercuryKey()}>✕</button>
            {:else}
              <input id="mk" class="ep-in" type="password" placeholder="Inception / Mercury key" bind:value={mkInput} />
              <button class="add" disabled={!mkInput.trim()} onclick={saveMercury}>Save</button>
            {/if}
          </div>
          <p class="hint">Stored locally; never shown again. Falls back to <code>INCEPTION_API_KEY</code> in <code>.env</code> if unset. Applies on your next vault open.</p>
        </div>

        <div class="card">
          <h2>Narration{#if !dwell.ttsAvailable}<span class="note"> · browser voice</span>{/if}</h2>
          <label class="chk"><input type="checkbox" checked={dwell.narrate} onchange={() => dwell.toggleNarrate()} /> 🔊 read pages aloud</label>
          <div class="color-row"><label for="tv">Voice</label>
            <select id="tv" value={dwell.ttsVoice} onchange={(e) => dwell.setTtsVoice(e.currentTarget.value)} disabled={!dwell.ttsAvailable}>
              {#each dwell.ttsVoices as v}<option value={v}>{v}</option>{/each}
            </select></div>
          <div class="color-row"><label for="tsp">Speed</label>
            <input id="tsp" type="range" min="0.7" max="1.5" step="0.05" value={dwell.ttsSpeed} oninput={(e) => dwell.setTtsSpeed(+e.currentTarget.value)} /></div>
          <div class="color-row"><label for="tvol">Volume</label>
            <input id="tvol" type="range" min="0" max="1" step="0.05" value={dwell.ttsVolume} oninput={(e) => dwell.setTtsVolume(+e.currentTarget.value)} /></div>
        </div>

        <div class="card">
          <h2>Quizzes</h2>
          <label class="chk"><input type="checkbox" checked={dwell.quizzesOn} onchange={(e) => dwell.setQuizzes(e.currentTarget.checked)} /> ✎ quiz me as I read</label>
          <div class="color-row" class:off={!dwell.quizzesOn}><label for="qe">Every</label>
            <input id="qe" type="range" min="2" max="20" step="1" value={dwell.quizEvery} disabled={!dwell.quizzesOn} oninput={(e) => dwell.setQuizEvery(+e.currentTarget.value)} />
            <span class="qv">{dwell.quizEvery} pages</span></div>
          <div class="color-row" class:off={!dwell.quizzesOn}><label for="qc">Questions</label>
            <input id="qc" type="range" min="3" max="25" step="1" value={dwell.quizCount} disabled={!dwell.quizzesOn} oninput={(e) => dwell.setQuizCount(+e.currentTarget.value)} />
            <span class="qv">{dwell.quizCount}</span></div>
          <div class="qtypes" class:off={!dwell.quizzesOn}>
            <span class="qt-h">Question types</span>
            {#each QUIZ_TYPE_KEYS as t (t)}
              <label class="chk"><input type="checkbox" checked={dwell.quizTypes[t]} disabled={!dwell.quizzesOn} onchange={(e) => dwell.setQuizType(t, e.currentTarget.checked)} /> {QUIZ_TYPE_LABELS[t]}</label>
            {/each}
          </div>
        </div>
      {/if}

      {#if tab === 'learn'}
        <div class="card">
          <h2>Providers &amp; keys</h2>
          {#if dwell.endpoints.length}
            <ul class="eps">
              {#each dwell.endpoints as e (e.id)}
                <li class:off={!e.enabled}>
                  <span class="ep-dot {e.enabled ? 'on' : ''}"></span>
                  <span class="ep-name">{e.name}</span>
                  <span class="ep-prov">{e.provider}</span>
                  <span class="ep-models" title="discovered models">{e.models.length} models</span>
                  <button class="ep-act" title={e.enabled ? 'disable' : 'enable'} onclick={() => dwell.toggleEndpoint(e)}>{e.enabled ? '◉' : '○'}</button>
                  <button class="ep-act" title="re-probe models" onclick={() => dwell.reprobeEndpoint(e.id)}>↻</button>
                  <button class="ep-act rm" title="remove" onclick={() => dwell.removeEndpoint(e.id)}>✕</button>
                </li>
              {/each}
            </ul>
          {:else}
            <p class="hint">No endpoints yet. Add one below to use any provider's models — your key is stored locally and never shown again.</p>
          {/if}
          {#if addingEp}
            <div class="ep-form">
              <div class="ep-presets">
                <span class="pre-label">Quick add</span>
                {#each EP_PRESETS as p (p.name)}
                  <button class="ep-preset" class:on={epUrl === p.url} onclick={() => applyPreset(p)}>{p.name}</button>
                {/each}
              </div>
              <input class="ep-in" type="text" placeholder="Name (e.g. OpenAI)" bind:value={epName} />
              <input class="ep-in" type="text" placeholder="Base URL (e.g. https://api.openai.com/v1)" bind:value={epUrl} />
              <input class="ep-in" type="password" placeholder="API key (blank for local)" bind:value={epKey} />
              {#if epErr}<p class="ep-err">{epErr}</p>{/if}
              <div class="ep-btns">
                <button class="add" disabled={dwell.endpointsBusy || epUrl.trim().length < 8} onclick={saveEndpoint}>{dwell.endpointsBusy ? 'Adding…' : 'Add'}</button>
                <button class="disclosure" onclick={() => { addingEp = false; epErr = ''; }}>Cancel</button>
              </div>
              <p class="hint">Anything OpenAI-compatible (OpenAI, OpenRouter, Groq, Together, local Ollama/vLLM…) or Anthropic. OpenRouter (one key → every model) is the easy button.</p>
            </div>
          {:else}
            <button class="add" onclick={() => { addingEp = true; epName = ''; epUrl = ''; epKey = ''; epErr = ''; }}>+ Add endpoint</button>
          {/if}
        </div>

        <div class="card">
          <h2>Budget</h2>
          <div class="color-row"><label for="lpc">Per-source cap</label>
            <span class="dollar">$</span><input id="lpc" class="num" type="number" min="0" step="0.5" value={dwell.learnMaxCost}
              oninput={(e) => dwell.setLearnSetting('learnMaxCost', Math.max(0, +e.currentTarget.value))} /></div>
          <div class="color-row"><label for="ltc">Total build cap</label>
            <span class="dollar">$</span><input id="ltc" class="num" type="number" min="0" step="1" value={dwell.learnTotalCap}
              oninput={(e) => dwell.setLearnSetting('learnTotalCap', Math.max(0, +e.currentTarget.value))} /></div>
          <p class="hint">Per-source caps one document; the total cap halts the whole build (and tells you in the sidebar). 0 = unlimited.</p>
        </div>

        {#snippet modelOptions()}
          <option value="">First model{epModels?.[0] ? ` (${epModels[0].split('/').pop()})` : ''}</option>
          {#each epModels ?? [] as m (m)}<option value={m}>{m}</option>{/each}
        {/snippet}
        <div class="card">
          <h2>Models <span class="note">· {epLabel}</span></h2>
          <div class="color-row"><label for="lep">Provider</label>
            <select id="lep" value={dwell.learnEndpointId} onchange={(e) => dwell.setLearnEndpoint(e.currentTarget.value)}>
              {#if !learnEnabledEndpoints.length}
                <option value="">No providers — add one above</option>
              {:else}
                <option value="" disabled>Select a provider…</option>
                {#each learnEnabledEndpoints as e (e.id)}<option value={e.id}>{e.name} · {e.provider}</option>{/each}
              {/if}
            </select></div>
          {#if learnEp}
            <div class="color-row"><label for="lmo">Orchestrator</label>
              <select id="lmo" value={dwell.learnModelOrchestrator} onchange={(e) => dwell.setLearnSetting('learnModelOrchestrator', e.currentTarget.value)}>{@render modelOptions()}</select></div>
            <div class="color-row"><label for="lmw">Writer</label>
              <select id="lmw" value={dwell.learnModelWriter} onchange={(e) => dwell.setLearnSetting('learnModelWriter', e.currentTarget.value)}>{@render modelOptions()}</select></div>
            <p class="hint">The orchestrator plans which pages to write; the writer drafts each one. Ingest runs on <strong>{epLabel}</strong>.</p>
            <button class="disclosure" onclick={() => (advancedModel = !advancedModel)}>{advancedModel ? '▾' : '▸'} Advanced</button>
            {#if advancedModel}
              <div class="color-row"><label for="lmm">Mechanical</label>
                <select id="lmm" value={dwell.learnModelMechanical} onchange={(e) => dwell.setLearnSetting('learnModelMechanical', e.currentTarget.value)}>{@render modelOptions()}</select></div>
              <p class="hint">Cheap formatting / cleanup calls. Leave on default unless you know you want to change it.</p>
            {/if}
          {:else}
            <p class="hint">Add a provider in <strong>Providers &amp; keys</strong> above, then choose its models here.</p>
          {/if}
        </div>

        <div class="card">
          <h2>Web search <span class="note">· research prompts</span></h2>
          <div class="color-row"><label for="sprov">Provider</label>
            <select id="sprov" bind:value={searchProv}>
              {#each dwell.searchProviders as p (p)}<option value={p}>{p === 'tavily' ? 'Tavily' : p === 'brave' ? 'Brave' : p === 'jina' ? 'Jina' : p}</option>{/each}
            </select></div>
          <div class="color-row"><label for="skey">API key</label>
            {#if dwell.searchHasKey}
              <span class="ep-models">set ✓ ({dwell.searchProvider})</span>
              <button class="ep-act rm" title="remove" onclick={() => dwell.clearSearchKey()}>✕</button>
            {:else}
              <input id="skey" class="ep-in" type="password" placeholder="search API key" bind:value={searchKey} />
              <button class="add" disabled={!searchKey.trim()} onclick={saveSearch}>Save</button>
            {/if}
          </div>
          <p class="hint">A research prompt fans out web searches (and explores your graph's open nodes) to find new material. Use <a href="https://jina.ai" target="_blank" rel="noopener">Jina</a> (search + reads JS/GitHub pages), <a href="https://tavily.com" target="_blank" rel="noopener">Tavily</a>, or <a href="https://brave.com/search/api" target="_blank" rel="noopener">Brave</a>. {dwell.searchAvailable ? 'Search is ready.' : 'Not set — research prompts won’t run.'}</p>
        </div>

        <div class="card">
          <h2>Pipeline</h2>
          <label class="chk"><input type="checkbox" checked={dwell.learnAutoExplore} onchange={(e) => dwell.setLearnSetting('learnAutoExplore', e.currentTarget.checked)} /> ✦ auto-explore (suggest expansions after ingest)</label>
          <div class="color-row"><label for="lmp">Max pages / source</label>
            <input id="lmp" class="num" type="number" min="0" max="100" step="1" value={dwell.learnMaxPages}
              oninput={(e) => dwell.setLearnSetting('learnMaxPages', Math.max(0, Math.min(100, +e.currentTarget.value)))} /></div>
          <p class="hint">Caps how many wiki pages one source can create. 0 = pipeline default (25).</p>
        </div>
      {/if}
    </div>

    {#if dwell.pendingAxes}
      <div class="apply-bar">
        <span class="apply-note">Style changes pending</span>
        <button class="apply-btn" onclick={() => dwell.applyAxes()}>↻ Apply &amp; re-render</button>
      </div>
    {/if}
  </div>
</div>

<style>
  /* No backdrop: transparent + non-blocking so the live theme preview behind reads
     true (no dimming colour-shift) and the page stays interactive. Close via ✕. */
  .overlay { position: fixed; inset: 0; z-index: 90; pointer-events: none; }
  .win {
    pointer-events: auto;
    position: fixed; left: 50%; top: 50%; transform: translate(-50%, -50%);
    width: min(440px, 94vw); max-height: 88vh; display: flex; flex-direction: column;
    background: var(--bg); border: 1px solid var(--border); border-radius: 12px;
    box-shadow: 0 16px 60px #000a; overflow: hidden;
  }
  .titlebar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 9px 12px; border-bottom: 1px solid var(--border); cursor: move; user-select: none;
    background: var(--panel);
  }
  .title { font-weight: 600; font-size: 13px; }
  .x { background: none; color: var(--meta); padding: 2px 7px; }
  .x:hover { background: var(--hover); color: var(--fg); }

  /* Odysseus admin-tabs */
  .tabs { display: flex; gap: 0; border-bottom: 1px solid var(--border); padding: 0 8px; background: var(--panel); }
  .tab { background: none; border: none; border-bottom: 2px solid transparent; color: var(--meta); font-size: 12px; padding: 8px 13px; border-radius: 0; }
  .tab:hover { color: var(--fg); background: transparent; }
  .tab.active { color: var(--accent); border-bottom-color: var(--accent); }

  .body { padding: 12px; overflow-y: auto; }

  /* Odysseus admin-card */
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin-bottom: 10px; }
  .card h2 { font-size: 13px; font-weight: 600; letter-spacing: -0.02em; margin: 0 0 8px; padding-bottom: 6px; border-bottom: 1px solid color-mix(in srgb, var(--border) 40%, transparent); }

  /* theme swatch grid */
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(70px, 1fr)); gap: 7px; }
  .swatch-wrap { position: relative; }
  .swatch {
    width: 100%; border: 2px solid var(--border); border-radius: 8px; cursor: pointer;
    padding: 6px 4px; text-align: center; font-size: .68rem; color: var(--fg); background: transparent;
    transition: border-color .15s, transform .15s; display: flex; flex-direction: column; align-items: center; gap: 3px;
  }
  .swatch:hover { transform: scale(1.06); background: transparent; }
  .swatch.active { border-color: var(--accent); box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 33%, transparent); }
  .dots { display: flex; justify-content: center; }
  .dots span { width: 15px; height: 15px; border-radius: 50%; margin-left: -5px; border: 1.5px solid color-mix(in srgb, var(--fg) 14%, transparent); }
  .dots span:first-child { margin-left: 0; }
  .sw-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%; }
  .del { position: absolute; top: -5px; right: -5px; width: 19px; height: 19px; padding: 0; border-radius: 50%;
    background: var(--err); color: #fff; font-size: 10px; line-height: 1; opacity: 0; transition: opacity .15s; }
  .swatch-wrap:hover .del { opacity: 1; }

  /* color rows */
  .color-row { display: flex; align-items: center; gap: 6px; padding: 4px 4px; border-radius: 6px; }
  .color-row:hover { background: color-mix(in srgb, var(--accent) 8%, transparent); }
  .color-row.off { opacity: .45; }
  .color-row label { font-size: 13px; font-weight: 500; color: var(--fg); opacity: .8; flex: 1; }
  .color-row input[type="color"] { width: 26px; height: 26px; border: 1px solid var(--border); border-radius: 50%; background: none; cursor: pointer; padding: 0; overflow: hidden; flex-shrink: 0; }
  .color-row select { max-width: 150px; }
  .color-row input[type="range"] { width: 150px; }
  .reset { width: 22px; height: 22px; border: none; background: none; color: var(--fg); opacity: 0; font-size: 1.05rem; padding: 0; line-height: 1; flex-shrink: 0; pointer-events: none; transition: opacity .15s, color .15s; }
  .reset.changed { opacity: .45; pointer-events: auto; }
  .reset.changed:hover { opacity: 1; color: var(--accent); }
  .save-row { display: flex; gap: 6px; margin-top: 10px; }
  .save-row .name { flex: 1; }
  .chk { display: flex; align-items: center; gap: 6px; padding: 5px 4px; font-size: 13px; color: var(--fg); }
  .note { font-weight: 400; color: var(--meta); font-size: 11px; }
  .qv { font-size: 12px; color: var(--meta); min-width: 58px; text-align: right; font-variant-numeric: tabular-nums; }
  .num { width: 72px; text-align: right; font-variant-numeric: tabular-nums; padding: 4px 7px; background: var(--panel); color: var(--fg); border: 1px solid var(--border); border-radius: 6px; font-family: inherit; font-size: 13px; }
  .num:focus { outline: none; border-color: var(--accent); }
  .dollar { font-size: 12.5px; color: var(--meta); }
  .hint { font-size: 11.5px; line-height: 1.5; color: var(--meta); margin: 4px 4px 2px; }
  .disclosure { background: none; border: none; color: var(--accent); font-size: 12px; cursor: pointer; padding: 4px; align-self: flex-start; }
  .eps { list-style: none; margin: 2px 0 10px; padding: 0; display: flex; flex-direction: column; gap: 4px; }
  .eps li { display: flex; align-items: center; gap: 8px; padding: 6px 8px; background: var(--panel); border: 1px solid var(--border); border-radius: 7px; }
  .eps li.off { opacity: .5; }
  .ep-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--border); flex: 0 0 auto; }
  .ep-dot.on { background: #3fb950; }
  .ep-name { font-size: 13px; color: var(--fg); font-weight: 500; flex: 1 1 auto; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ep-prov { font-size: 10px; text-transform: uppercase; letter-spacing: .06em; color: var(--bg); background: var(--accent); border-radius: 4px; padding: 1px 5px; flex: 0 0 auto; }
  .ep-models { font-size: 11px; color: var(--meta); flex: 0 0 auto; font-variant-numeric: tabular-nums; }
  .ep-act { background: none; border: none; color: var(--meta); cursor: pointer; font-size: 13px; padding: 0 3px; flex: 0 0 auto; }
  .ep-act:hover { color: var(--fg); }
  .ep-act.rm:hover { color: var(--err); }
  .ep-form { display: flex; flex-direction: column; gap: 6px; margin-top: 4px; }
  .ep-presets { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-bottom: 2px; }
  .pre-label { font-size: 10px; text-transform: uppercase; letter-spacing: .1em; color: var(--meta); margin-right: 2px; }
  .ep-preset { background: var(--panel); color: var(--fg); border: 1px solid var(--border); border-radius: 6px; padding: 4px 10px; font-size: 12px; cursor: pointer; }
  .ep-preset:hover { border-color: var(--accent); }
  .ep-preset.on { border-color: var(--accent); color: var(--accent); background: color-mix(in srgb, var(--accent) 12%, transparent); }
  .ep-in { padding: 7px 9px; background: var(--panel); color: var(--fg); border: 1px solid var(--border); border-radius: 7px; font-family: inherit; font-size: 13px; }
  .ep-in:focus { outline: none; border-color: var(--accent); }
  .ep-err { color: var(--err); font-size: 11.5px; margin: 0; }
  .ep-btns { display: flex; gap: 8px; align-items: center; }
  .qtypes { display: grid; grid-template-columns: 1fr 1fr; gap: 1px 10px; margin-top: 8px; padding: 8px 4px 0;
    border-top: 1px solid color-mix(in srgb, var(--border) 40%, transparent); }
  .qtypes.off { opacity: .45; }
  .qtypes .qt-h { grid-column: 1 / -1; font-size: 11px; color: var(--meta); margin-bottom: 2px; }
  .qtypes .chk { font-size: 12.5px; padding: 3px 4px; }

  /* sticky footer: flush deferred axis changes in one paid re-render */
  .apply-bar {
    display: flex; align-items: center; justify-content: space-between; gap: 10px;
    padding: 9px 12px; border-top: 1px solid var(--border); background: var(--panel);
  }
  .apply-note { font-size: 12px; color: var(--meta); }
  .apply-btn {
    font-size: 12.5px; font-weight: 600; padding: 6px 12px; border-radius: 7px;
    border: 1px solid var(--accent); background: var(--accent); color: var(--bg); cursor: pointer;
  }
  .apply-btn:hover { filter: brightness(1.08); }
</style>
