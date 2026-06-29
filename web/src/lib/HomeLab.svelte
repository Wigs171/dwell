<script lang="ts">
  // Dev play-area (open /#home-lab): the home hero — the DWELL wordmark diffusing
  // in and out of a field of jumbled text-diffusion characters, over masonry
  // gridlines — with live controls to tune the breath, the noise, the logo, the
  // gridlines, and the theme. Not part of the shipping shell.
  import DiffusionField from './DiffusionField.svelte';
  import { THEMES, writeTheme, themeByName } from './themes';

  let themeName = $state('claude');
  let diffuseFrac = $state(0.14);
  let diffuseLen = $state(6);
  let cellPref = $state(13);
  let logoFrac = $state(0.82);
  let vPos = $state(0.4);
  let noiseAlpha = $state(0.13);
  let logoAlpha = $state(0.95);
  let shuffleHz = $state(9);
  let accentChance = $state(0.05);
  let gridlines = $state(true);

  const theme = $derived(themeByName(themeName));
  $effect(() => { writeTheme(theme); });
</script>

<div class="lab">
  <div class="hero">
    <DiffusionField {diffuseFrac} {diffuseLen} {cellPref} {logoFrac} {vPos} {noiseAlpha} {logoAlpha} {shuffleHz} {accentChance} {gridlines} />
  </div>

  <aside class="panel">
    <div class="ttl">Hero · diffusion field</div>

    <div class="grp">
      <span class="lbl">Theme</span>
      <select bind:value={themeName}>
        {#each THEMES as t}<option value={t.name}>{t.name}</option>{/each}
      </select>
    </div>

    <div class="ttl2">Pixel diffusion</div>
    <div class="grp"><span class="lbl">Diffusing pixels <b>{(diffuseFrac * 100) | 0}%</b></span>
      <input type="range" min="0" max="0.6" step="0.01" bind:value={diffuseFrac} /></div>
    <div class="grp"><span class="lbl">Churn length <b>{diffuseLen}</b></span>
      <input type="range" min="1" max="20" step="1" bind:value={diffuseLen} /></div>

    <div class="ttl2">Jumble</div>
    <div class="grp"><span class="lbl">Noise opacity <b>{noiseAlpha.toFixed(2)}</b></span>
      <input type="range" min="0.03" max="0.4" step="0.01" bind:value={noiseAlpha} /></div>
    <div class="grp"><span class="lbl">Reshuffle <b>{shuffleHz}/s</b></span>
      <input type="range" min="1" max="24" step="1" bind:value={shuffleHz} /></div>
    <div class="grp"><span class="lbl">Accent chars <b>{(accentChance * 100) | 0}%</b></span>
      <input type="range" min="0" max="0.25" step="0.01" bind:value={accentChance} /></div>

    <div class="ttl2">Logo</div>
    <div class="grp"><span class="lbl">Char size <b>{cellPref}px</b></span>
      <input type="range" min="6" max="22" step="1" bind:value={cellPref} /></div>
    <div class="grp"><span class="lbl">Width <b>{(logoFrac * 100) | 0}%</b></span>
      <input type="range" min="0.4" max="0.98" step="0.02" bind:value={logoFrac} /></div>
    <div class="grp"><span class="lbl">Vertical pos <b>{(vPos * 100) | 0}%</b></span>
      <input type="range" min="0.1" max="0.8" step="0.02" bind:value={vPos} /></div>
    <div class="grp"><span class="lbl">Block ink <b>{logoAlpha.toFixed(2)}</b></span>
      <input type="range" min="0.4" max="1" step="0.02" bind:value={logoAlpha} /></div>

    <div class="grp"><label class="chk"><input type="checkbox" bind:checked={gridlines} /> masonry gridlines</label></div>

    <p class="hint">The logo is solid blocks (the book/E in the theme <b>accent</b>); individual pixels randomly churn through jumbled characters and resolve back to a block. <b>Diffusing pixels</b> sets how many churn at once — keep it low to stay legible. Canvas pauses in a hidden tab — watch in a foreground browser.</p>
  </aside>
</div>

<style>
  .lab { position: fixed; inset: 0; background: var(--bg); color: var(--fg); overflow: hidden; }
  .hero { position: absolute; inset: 0 278px 0 0; }   /* clear the control panel */

  .panel {
    position: absolute; top: 14px; right: 14px; z-index: 10; width: 250px; max-height: calc(100vh - 28px);
    overflow: auto; padding: 14px 14px 16px; border-radius: 12px;
    background: color-mix(in srgb, var(--panel) 86%, transparent);
    border: 1px solid var(--border); backdrop-filter: blur(10px); font-size: 12px;
  }
  .ttl { font-weight: 800; font-size: 13px; margin-bottom: 10px; }
  .ttl2 { font-weight: 700; font-size: 12px; color: var(--meta); margin: 12px 0 8px; text-transform: uppercase; letter-spacing: 0.06em; }
  .grp { margin-bottom: 11px; }
  .lbl { display: flex; justify-content: space-between; color: var(--meta); margin-bottom: 4px; }
  .lbl b { color: var(--fg); font-weight: 700; }
  input[type="range"] { width: 100%; accent-color: var(--accent); }
  select { width: 100%; background: var(--bg); color: var(--fg); border: 1px solid var(--border); border-radius: 7px; padding: 5px 7px; }
  .chk { display: flex; align-items: center; gap: 6px; cursor: pointer; color: var(--fg); }
  .chk input { accent-color: var(--accent); }
  .hint { margin: 12px 0 0; color: var(--meta); font-size: 11px; line-height: 1.5; font-style: italic; opacity: 0.85; }
</style>
