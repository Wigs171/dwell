<script lang="ts">
  import { dwell } from './lib/dwell.svelte';
  import Sidebar from './lib/Sidebar.svelte';
  import IconRail from './lib/IconRail.svelte';
  import TopBar from './lib/TopBar.svelte';
  import LaunchMenu from './lib/LaunchMenu.svelte';
  import VaultGallery from './lib/VaultGallery.svelte';
  import VaultDetail from './lib/VaultDetail.svelte';
  import Home from './lib/Home.svelte';
  import Learn from './lib/Learn.svelte';
  import Reader from './lib/Reader.svelte';
  import Branches from './lib/Branches.svelte';
  import Transport from './lib/Transport.svelte';
  import Quiz from './lib/Quiz.svelte';
  import SettingsWindow from './lib/SettingsWindow.svelte';
  import NotesWindow from './lib/NotesWindow.svelte';
  import LayoutLab from './lib/LayoutLab.svelte';
  import TextFigureLab from './lib/TextFigureLab.svelte';
  import HomeLab from './lib/HomeLab.svelte';

  // Dev harnesses: /#layouts inspects the image layouts, /#textfigures the
  // derived text-figures, /#home-lab the hero logo + sea-of-words background —
  // all in isolation, outside the app shell.
  const hash = typeof location !== 'undefined' ? location.hash : '';
  const showLab = hash === '#layouts';
  const showTextLab = hash === '#textfigures';
  const showHomeLab = hash === '#home-lab';
  if (typeof window !== 'undefined') window.addEventListener('hashchange', () => location.reload());

  if (!showLab && !showTextLab && !showHomeLab) dwell.init();
</script>

{#if showLab}
  <LayoutLab />
{:else if showTextLab}
  <TextFigureLab />
{:else if showHomeLab}
  <HomeLab />
{:else}
<div class="shell">
  {#if dwell.sidebarOpen}
    <Sidebar />
  {:else}
    <IconRail />
  {/if}

  <main>
    {#if dwell.page === 'home'}
      <Home />
    {:else if dwell.page === 'learn'}
      <Learn />
    {:else}
      <TopBar />
      {#if !dwell.session}
        <VaultGallery />
      {:else}
        {#if !dwell.started}
          <LaunchMenu />
        {/if}

        <Reader />

        {#if dwell.started}
          <Branches />
          <Transport />
        {/if}
      {/if}
    {/if}

    <footer class="status" class:err={dwell.statusErr}>{dwell.status}</footer>
  </main>
</div>

{#if dwell.vaultDetail}
  <VaultDetail />
{/if}

{#if dwell.quizOpen}
  <Quiz />
{/if}

{#if dwell.settingsOpen}
  <SettingsWindow />
{/if}
{#if dwell.notesOpen}
  <NotesWindow />
{/if}
{/if}

<style>
  .shell { flex: 1 1 auto; display: flex; flex-direction: row; min-height: 0; }
  main { flex: 1 1 auto; display: flex; flex-direction: column; min-width: 0; min-height: 0; background: transparent; }
  .status {
    background: var(--panel); border-top: 1px solid var(--border);
    color: var(--meta); font-size: 12px; padding: 6px 16px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .status.err { color: var(--err); }
</style>
