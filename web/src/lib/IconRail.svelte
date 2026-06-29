<script lang="ts">
  import { dwell } from './dwell.svelte';
  const ingestRunning = $derived(dwell.buildStatus === 'running');
  const ingestNotice = $derived(dwell.buildNotice);
</script>

<aside class="rail">
  <button class="logo" class:active={dwell.page === 'home'} title="Dwell — home" onclick={() => (dwell.page = 'home')}>◈</button>
  <button class="rb" title="expand sidebar" onclick={() => dwell.toggleSidebar()}>»</button>
  <span class="gap"></span>
  <!-- page nav (mirrors the open sidebar) -->
  <button class="rb nav" class:active={dwell.page === 'read'} title="Read" onclick={() => (dwell.page = 'read')}>▤</button>
  <button class="rb nav" class:active={dwell.page === 'learn'} title={ingestRunning ? 'ingest running — Learn' : ingestNotice && ingestNotice !== 'done' ? 'Learn — needs attention' : 'Learn'} onclick={() => dwell.openLearn()}>✦
    {#if ingestRunning}<span class="rdot run"></span>{:else if ingestNotice === 'done'}<span class="rdot ok"></span>{:else if ingestNotice}<span class="rdot err"></span>{/if}
  </button>
  <span class="gap"></span>
  {#if dwell.started}
    <button class="rb" title="new thread (surprise)" onclick={() => dwell.newThread()}>🎲</button>
  {/if}
  {#if dwell.session}
    <button class="rb" title="notes" onclick={() => (dwell.notesOpen = true)}>✎{#if dwell.notes.length}<span class="badge">{dwell.notes.length}</span>{/if}</button>
  {/if}
  <span class="spacer"></span>
  <button class="rb" title="settings" onclick={() => (dwell.settingsOpen = true)}>⚙</button>
</aside>

<style>
  .rail {
    width: 48px; flex: 0 0 48px; height: 100%;
    background: var(--panel); border-right: 1px solid var(--border);
    display: flex; flex-direction: column; align-items: center; gap: 4px;
    padding: 12px 4px;
  }
  .logo { background: none; color: var(--accent); font-size: 18px; padding: 6px; border-radius: 8px; cursor: pointer; }
  .logo.active { background: color-mix(in srgb, var(--accent) 15%, transparent); }
  .rb {
    width: 36px; height: 36px; background: transparent; color: var(--fg);
    border-radius: 8px; font-size: 15px; opacity: .6;
  }
  .rb:hover { opacity: 1; background: var(--hover); }
  .rb.nav.active { opacity: 1; color: var(--accent); background: color-mix(in srgb, var(--accent) 13%, transparent); }
  .rb { position: relative; }
  .badge {
    position: absolute; top: 2px; right: 2px; min-width: 13px; height: 13px; padding: 0 3px;
    border-radius: 7px; background: var(--accent); color: var(--bg);
    font-size: 9px; line-height: 13px; font-weight: 700;
  }
  .gap { flex: 0 0 6px; }
  .rdot { position: absolute; top: 5px; right: 5px; width: 7px; height: 7px; border-radius: 50%; }
  .rdot.run { background: var(--accent); animation: rpulse 1s ease-in-out infinite; }
  .rdot.ok { background: #3fb950; }
  .rdot.err { background: var(--err); }
  @keyframes rpulse { 0%, 100% { opacity: 1; } 50% { opacity: .3; } }
</style>
