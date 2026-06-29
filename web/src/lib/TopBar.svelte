<script lang="ts">
  import { dwell } from './dwell.svelte';

  const page = $derived(dwell.pages[dwell.pages.length - 1]);
  // The vault topic is "Short title — long description"; show only the title part up top
  // (the full topic stays in the hover tooltip). A page's own title is already short.
  const shortTopic = (t: string) => t.split(/\s+[—–]\s+/)[0].trim();
  const fullTopic = $derived(dwell.session?.topic ?? '');
  const title = $derived(page ? page.title : (shortTopic(fullTopic) || 'Dwell'));
  // the source doc(s) the current node was built from
  const source = $derived.by(() => {
    const src = page?.sources ?? [];
    if (!src.length) return '';
    return src.length > 1 ? `${src[0]} +${src.length - 1}` : src[0];
  });
</script>

<header class="topbar">
  {#if !dwell.sidebarOpen}
    <button class="ham" title="show sidebar" onclick={() => dwell.toggleSidebar()}>☰</button>
  {/if}
  <span class="title" title={page ? title : (fullTopic || title)}>{title}</span>
  {#if source}
    <span class="src" title="source: {(page?.sources ?? []).join(', ')}">· {source}</span>
  {/if}
  <span class="spacer"></span>
  {#if dwell.session}
    <span class="badge">{dwell.session.provider}{dwell.session.dry ? ' · free' : ''}</span>
  {/if}
</header>

<style>
  .topbar {
    display: flex; align-items: center; gap: 10px; padding: 8px 16px;
    border-bottom: 1px solid var(--border); background: transparent; min-height: 44px;
  }
  .ham { background: none; color: var(--fg); font-size: 17px; padding: 2px 8px; }
  .ham:hover { background: var(--hover); }
  .title { font-weight: 600; font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 0 1 auto; }
  .src { font-size: 12px; color: var(--meta); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 0 1 auto; }
  .badge {
    font-size: 11px; color: var(--meta); border: 1px solid var(--border);
    border-radius: 999px; padding: 2px 9px;
  }
</style>
