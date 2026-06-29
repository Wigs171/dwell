<script lang="ts">
  import { dwell } from './dwell.svelte';

  // Draggable floating window (same mechanics as SettingsWindow).
  let win = $state<HTMLDivElement>();
  let x = $state<number | null>(null);
  let y = $state<number | null>(null);
  let drag: { dx: number; dy: number } | null = null;

  function down(e: MouseEvent) {
    const r = win!.getBoundingClientRect();
    drag = { dx: e.clientX - r.left, dy: e.clientY - r.top }; x = r.left; y = r.top;
    window.addEventListener('mousemove', move); window.addEventListener('mouseup', up);
  }
  function move(e: MouseEvent) {
    if (!drag) return;
    x = Math.max(0, Math.min(window.innerWidth - 90, e.clientX - drag.dx));
    y = Math.max(0, Math.min(window.innerHeight - 36, e.clientY - drag.dy));
  }
  function up() { drag = null; window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); }

  const fmt = (ts: number) => new Date(ts).toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
</script>

<div class="overlay">
  <div class="win" bind:this={win} role="dialog" aria-label="Notes"
       style={x !== null ? `left:${x}px; top:${y}px; transform:none;` : ''}>
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="titlebar" onmousedown={down}>
      <span class="title">✎ Notes <span class="count">{dwell.notes.length}</span></span>
      <span class="tb-actions">
        {#if dwell.notes.length}
          <button class="link clr" onclick={() => dwell.clearNotes()} title="remove all notes">clear all</button>
        {/if}
        <button class="x" onclick={() => (dwell.notesOpen = false)} title="close">✕</button>
      </span>
    </div>

    <div class="body">
      {#if !dwell.notes.length}
        <p class="empty">No notes yet. Highlight a passage while reading and click <b>✎ Note</b>.</p>
      {:else}
        {#each dwell.notes as n (n.id)}
          <div class="note">
            <button class="del" onclick={() => dwell.removeNote(n.id)} title="delete">✕</button>
            <blockquote>{n.text}</blockquote>
            <div class="meta">
              <button class="src" onclick={() => dwell.gotoNote(n)} title="return to this page">↩ {n.title}</button>
              <span class="when">{fmt(n.ts)}</span>
            </div>
          </div>
        {/each}
      {/if}
    </div>
  </div>
</div>

<style>
  .overlay { position: fixed; inset: 0; z-index: 90; pointer-events: none; }
  .win {
    pointer-events: auto;
    position: fixed; left: 50%; top: 22%; transform: translate(-50%, 0);
    width: min(380px, 92vw); max-height: 76vh; display: flex; flex-direction: column;
    background: var(--bg); border: 1px solid var(--border); border-radius: 12px;
    box-shadow: 0 16px 60px #000a; overflow: hidden;
  }
  .titlebar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 9px 12px; border-bottom: 1px solid var(--border); cursor: move; user-select: none;
    background: var(--panel);
  }
  .title { font-weight: 600; font-size: 13px; }
  .count { color: var(--meta); font-weight: 400; margin-left: 2px; }
  .tb-actions { display: flex; align-items: center; gap: 6px; }
  .link { background: none; border: none; color: var(--meta); font-size: 11px; cursor: pointer; padding: 2px 4px; }
  .link:hover { color: var(--fg); }
  .x { background: none; color: var(--meta); padding: 2px 7px; }
  .x:hover { background: var(--hover); color: var(--fg); }

  .body { padding: 10px; overflow-y: auto; }
  .empty { color: var(--meta); font-size: 13px; text-align: center; padding: 24px 12px; line-height: 1.5; }

  .note {
    position: relative; background: var(--panel); border: 1px solid var(--border);
    border-radius: 8px; padding: 10px 12px; margin-bottom: 9px;
  }
  .note blockquote {
    margin: 0 0 8px; padding-left: 9px; border-left: 2px solid var(--accent);
    font-size: 13px; line-height: 1.5; color: var(--fg);
  }
  .meta { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
  .src {
    background: none; border: none; color: var(--accent); font-size: 12px; cursor: pointer;
    padding: 0; text-align: left; max-width: 80%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .src:hover { text-decoration: underline; }
  .when { font-size: 11px; color: var(--meta); font-variant-numeric: tabular-nums; flex-shrink: 0; }
  .del {
    position: absolute; top: 6px; right: 6px; width: 19px; height: 19px; padding: 0; border-radius: 50%;
    background: none; color: var(--meta); font-size: 11px; line-height: 1; opacity: 0; transition: opacity .15s;
  }
  .note:hover .del { opacity: 1; }
  .del:hover { background: var(--err); color: #fff; }
</style>
