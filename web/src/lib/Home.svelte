<script lang="ts">
  // Home — the DWELL wordmark diffusing in and out of a field of jumbled
  // text-diffusion characters (DiffusionField). Beneath it, the tagline forms out
  // of noise, holds, and deteriorates back into noise as the next phrase — the
  // original self-narrating subtext. Tune the field at /#home-lab.
  import { onMount, onDestroy } from 'svelte';
  import DiffusionField from './DiffusionField.svelte';

  type Cell = { ch: string; noise: boolean };
  const NOISE = 'ABCDEFGHJKLMNPQRSTUVWXYZ0123456789#%&$@?<>/\\=+*';
  const rnd = (n: number) => Math.floor(Math.random() * n);
  const noiseCh = () => NOISE[rnd(NOISE.length)];
  const jumble = (n: number) => Array.from({ length: n }, noiseCh).join('');
  const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

  // Animates from pure noise toward `target`, each glyph locking in at its own
  // random frame. Resolves when every glyph has settled.
  function scrambler(set: (cells: Cell[]) => void) {
    let raf = 0, frame = 0, done: (() => void) | null = null;
    let q: { to: string; end: number; ch: string }[] = [];
    function tick() {
      const cells: Cell[] = [];
      let settled = 0;
      for (const c of q) {
        if (frame >= c.end) { cells.push({ ch: c.to, noise: false }); settled++; }
        else {
          if (!c.ch || Math.random() < 0.3) c.ch = c.to === ' ' ? ' ' : noiseCh();
          cells.push({ ch: c.ch, noise: true });
        }
      }
      set(cells);
      if (settled === q.length) { done?.(); }
      else { frame++; raf = requestAnimationFrame(tick); }
    }
    return {
      to(target: string, base = 10, spread = 42): Promise<void> {
        cancelAnimationFrame(raf);
        q = [...target].map((to) => ({ to, end: base + rnd(spread), ch: '' }));
        frame = 0;
        return new Promise((res) => { done = res; tick(); });
      },
      stop() { cancelAnimationFrame(raf); },
    };
  }

  const PHRASES = [
    'An endless reading of any knowledge base',
    'Read at your pace; wander at your whim',
    'Curiosity is the only map',
    'Knowledge, rendered as you read',
    'A reading with no last page',
    'Dwell, Wander, Understand',
  ];

  let phraseCells = $state<Cell[]>([]);
  let alive = true;
  const phrase = scrambler((c) => (phraseCells = c));

  async function phraseLoop() {
    let i = 0;
    await sleep(700);
    while (alive) {
      await phrase.to(PHRASES[i]);                 // form
      if (!alive) break;
      await sleep(2600);                           // hold
      if (!alive) break;
      await phrase.to(jumble(PHRASES[i].length));  // deteriorate back into noise
      if (!alive) break;
      await sleep(450);
      i = (i + 1) % PHRASES.length;
    }
  }

  onMount(() => { phraseLoop(); });
  onDestroy(() => { alive = false; phrase.stop(); });
</script>

<div class="home">
  <DiffusionField noise={false} gridlines={false} />
  <div class="phrase" aria-hidden="true">
    {#each phraseCells as c, i (i)}<span class:noise={c.noise}>{c.ch}</span>{/each}
  </div>
</div>

<style>
  .home { position: relative; flex: 1 1 auto; min-height: 0; overflow: hidden; }
  .phrase {
    position: absolute; left: 0; right: 0; bottom: 12%; z-index: 1; text-align: center;
    min-height: 1.6em; white-space: pre-wrap;
    font-family: 'Consolas', ui-monospace, 'SF Mono', monospace;
    font-size: clamp(14px, 2.3vw, 21px); letter-spacing: .02em; color: var(--meta);
  }
  .phrase span.noise { color: var(--accent); opacity: .5; }
</style>
