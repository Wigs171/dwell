<script lang="ts">
  import { dwell } from './dwell.svelte';
  import type { QuizQuestion } from './types';

  // Draggable, non-blocking window — the deck stays live so the reader can flip back.
  let win = $state<HTMLDivElement>();
  let x = $state<number | null>(null);
  let y = $state<number | null>(null);
  let drag: { dx: number; dy: number } | null = null;

  // per-question response state (keyed by index)
  let pick = $state<Record<number, number>>({});            // choice
  let tfPick = $state<Record<number, boolean>>({});          // truefalse
  let clozeIn = $state<Record<number, string>>({});          // cloze input
  let clozeDone = $state<Record<number, boolean>>({});       // cloze submitted
  let recall = $state<Record<number, string>>({});           // recall input
  let revealed = $state<Record<number, boolean>>({});        // recall revealed
  let gotIt = $state<Record<number, boolean | undefined>>({}); // recall self-rating
  let matchPick = $state<Record<number, Record<number, string>>>({}); // matching: leftIdx → right
  let matchDone = $state<Record<number, boolean>>({});       // matching checked

  const qs = $derived<QuizQuestion[]>(dwell.quiz ?? []);

  // shuffled right-column for each matching question (computed once per quiz)
  let shuffledRights = $state<Record<number, string[]>>({});
  $effect(() => {
    const m: Record<number, string[]> = {};
    qs.forEach((q, i) => {
      if (q.type === 'matching' && q.pairs) {
        const r = q.pairs.map((p) => p.right);
        for (let j = r.length - 1; j > 0; j--) { const k = Math.floor(Math.random() * (j + 1)); [r[j], r[k]] = [r[k], r[j]]; }
        m[i] = r;
      }
    });
    shuffledRights = m;
  });

  const norm = (s: string) => (s || '').toLowerCase().replace(/[^a-z0-9 ]/g, '').replace(/\s+/g, ' ').trim();
  function clozeOk(i: number, q: QuizQuestion) {
    const a = norm(clozeIn[i] ?? ''), b = norm(q.blank ?? '');
    return !!a && !!b && (a === b || a.includes(b) || b.includes(a));
  }
  function isDone(q: QuizQuestion, i: number): boolean {
    switch (q.type) {
      case 'choice': return pick[i] !== undefined;
      case 'truefalse': return tfPick[i] !== undefined;
      case 'cloze': return !!clozeDone[i];
      case 'recall': return gotIt[i] !== undefined;
      case 'matching': return !!matchDone[i];
    }
  }
  function isCorrect(q: QuizQuestion, i: number): boolean {
    switch (q.type) {
      case 'choice': return pick[i] === q.correct;
      case 'truefalse': return tfPick[i] === q.tf;
      case 'cloze': return clozeOk(i, q);
      case 'recall': return gotIt[i] === true;
      case 'matching': return !!q.pairs && q.pairs.every((p, li) => matchPick[i]?.[li] === p.right);
    }
  }
  const score = $derived.by(() => qs.reduce((s, q, i) => s + (isDone(q, i) && isCorrect(q, i) ? 1 : 0), 0));
  const allDone = $derived(qs.length > 0 && qs.every((q, i) => isDone(q, i)));

  function pickChoice(i: number, o: number) { if (pick[i] === undefined) pick = { ...pick, [i]: o }; }
  function pickTF(i: number, v: boolean) { if (tfPick[i] === undefined) tfPick = { ...tfPick, [i]: v }; }
  function submitCloze(i: number) { clozeDone = { ...clozeDone, [i]: true }; }
  function setMatch(i: number, li: number, v: string) { matchPick = { ...matchPick, [i]: { ...(matchPick[i] ?? {}), [li]: v } }; }
  const matchAllPicked = (q: QuizQuestion, i: number) => !!q.pairs && q.pairs.every((_, li) => matchPick[i]?.[li]);

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
</script>

<div class="quizwin" bind:this={win} role="dialog" aria-label="Quiz"
     style={x !== null ? `left:${x}px; top:${y}px; right:auto; transform:none;` : ''}>
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="titlebar" onmousedown={down}>
    <span class="title">✎ Quick check<span class="dim"> · last {dwell.quizEvery} pages</span></span>
    {#if qs.length}<span class="score">{score} / {qs.length}</span>{/if}
    <button class="x" onclick={() => dwell.closeQuiz()} title="finish & continue reading">✕</button>
  </div>

  <div class="body">
    {#if dwell.quizLoading}
      <div class="state">Composing your quiz…</div>
    {:else if !qs.length}
      <div class="state">No quiz this time — carry on.</div>
    {:else}
      <div class="hint">📖 Drag me aside and flip back through the pages — the answers are highlighted while this is open.</div>
      {#each qs as q, i (i)}
        <article class="q">
          <div class="stem"><b>{i + 1}.</b> {q.q}</div>

          {#if q.type === 'choice'}
            <div class="opts">
              {#each q.options ?? [] as opt, o (o)}
                <button class="opt" class:correct={pick[i] !== undefined && o === q.correct}
                  class:wrong={pick[i] === o && o !== q.correct}
                  disabled={pick[i] !== undefined} onclick={() => pickChoice(i, o)}>{opt}</button>
              {/each}
            </div>

          {:else if q.type === 'truefalse'}
            <div class="tf">
              {#each [{ v: true, l: 'True' }, { v: false, l: 'False' }] as b (b.l)}
                <button class="opt" class:correct={tfPick[i] !== undefined && b.v === q.tf}
                  class:wrong={tfPick[i] === b.v && b.v !== q.tf}
                  disabled={tfPick[i] !== undefined} onclick={() => pickTF(i, b.v)}>{b.l}</button>
              {/each}
            </div>

          {:else if q.type === 'cloze'}
            <div class="cloze">
              <input type="text" placeholder="fill the blank…" bind:value={clozeIn[i]} disabled={clozeDone[i]}
                onkeydown={(e) => { if (e.key === 'Enter' && (clozeIn[i] ?? '').trim()) submitCloze(i); }} />
              {#if !clozeDone[i]}
                <button onclick={() => submitCloze(i)} disabled={!(clozeIn[i] ?? '').trim()}>Check</button>
              {/if}
            </div>
            {#if clozeDone[i]}
              <div class="why" class:ok={clozeOk(i, q)}>{clozeOk(i, q) ? '✓ ' : '✗ '}Answer: <b>{q.blank}</b></div>
            {/if}

          {:else if q.type === 'matching'}
            <div class="match">
              {#each q.pairs ?? [] as p, li (li)}
                <div class="match-row" class:correct={matchDone[i] && matchPick[i]?.[li] === p.right}
                  class:wrong={matchDone[i] && matchPick[i]?.[li] !== p.right}>
                  <span class="left">{p.left}</span>
                  <select value={matchPick[i]?.[li] ?? ''} disabled={matchDone[i]} onchange={(e) => setMatch(i, li, e.currentTarget.value)}>
                    <option value="" disabled>choose…</option>
                    {#each shuffledRights[i] ?? [] as r (r)}<option value={r}>{r}</option>{/each}
                  </select>
                </div>
              {/each}
              {#if !matchDone[i]}
                <button class="check" disabled={!matchAllPicked(q, i)} onclick={() => (matchDone = { ...matchDone, [i]: true })}>Check</button>
              {:else if !isCorrect(q, i)}
                <div class="why">Correct: {#each q.pairs ?? [] as p (p.left)}<span class="ans">{p.left} → {p.right}</span>{/each}</div>
              {/if}
            </div>

          {:else}
            <textarea class="recall" rows="3" placeholder="Answer in your own words…"
              bind:value={recall[i]} disabled={revealed[i]}></textarea>
            {#if !revealed[i]}
              <button class="reveal" onclick={() => (revealed = { ...revealed, [i]: true })}>Reveal answer</button>
            {:else}
              <div class="ideal"><b>Answer.</b> {q.ideal}</div>
              <div class="rate"><span class="dim">How did you do?</span>
                <button class:on={gotIt[i] === true} onclick={() => (gotIt = { ...gotIt, [i]: true })}>Got it</button>
                <button class:on={gotIt[i] === false} onclick={() => (gotIt = { ...gotIt, [i]: false })}>Missed it</button>
              </div>
            {/if}
          {/if}

          {#if isDone(q, i) && q.why && q.type !== 'matching'}<div class="why">{q.why}</div>{/if}
        </article>
      {/each}
    {/if}
  </div>

  <footer>
    <button class="ghost" onclick={() => dwell.disableQuizzes()}>Turn off quizzes</button>
    <span class="spacer"></span>
    <button class="primary" onclick={() => dwell.closeQuiz()}>{allDone ? 'Continue reading →' : 'Skip →'}</button>
  </footer>
</div>

<style>
  .quizwin {
    position: fixed; right: 22px; top: 50%; transform: translateY(-50%); z-index: 70;
    width: min(400px, 92vw); max-height: 84vh; display: flex; flex-direction: column;
    background: var(--bg); color: var(--ink);
    border: 1px solid var(--border); border-radius: 12px; box-shadow: 0 16px 60px #000a;
  }
  .titlebar {
    display: flex; align-items: center; gap: 10px; padding: 9px 12px;
    border-bottom: 1px solid var(--border); cursor: move; user-select: none;
    background: var(--panel); border-radius: 12px 12px 0 0;
  }
  .title { font-weight: 600; font-size: 13px; }
  .score { margin-left: auto; font-family: Consolas, monospace; font-size: 13px; color: var(--meta); }
  .x { background: none; color: var(--meta); padding: 2px 7px; }
  .x:hover { background: var(--hover); color: var(--fg); }

  .state { padding: 30px 16px; color: var(--meta); font-style: italic; }
  .hint { font-size: 12px; color: var(--meta); line-height: 1.45; padding: 2px 2px 10px;
    border-bottom: 1px solid color-mix(in srgb, var(--border) 50%, transparent); margin-bottom: 12px; }

  .body { padding: 14px 16px; overflow-y: auto; display: flex; flex-direction: column; gap: 18px; }
  .q { display: flex; flex-direction: column; gap: 9px; }
  .stem { font-family: Georgia, serif; font-size: 15.5px; line-height: 1.45; }

  .opts { display: flex; flex-direction: column; gap: 7px; }
  .tf { display: flex; gap: 8px; }
  .tf .opt { flex: 1; text-align: center; }
  .opt {
    text-align: left; line-height: 1.35; padding: 9px 12px; border-radius: 9px;
    background: var(--opt); color: var(--opt-fg); border: 1px solid transparent;
  }
  .opt:disabled { opacity: 1; cursor: default; }
  .opt.correct { background: color-mix(in srgb, var(--ready) 26%, var(--pane)); border-color: var(--ready); }
  .opt.wrong { background: color-mix(in srgb, var(--err) 22%, var(--pane)); border-color: var(--err); }

  .cloze { display: flex; gap: 7px; }
  .cloze input { flex: 1; }
  .match { display: flex; flex-direction: column; gap: 6px; }
  .match-row { display: flex; align-items: center; gap: 8px; padding: 4px 6px; border-radius: 8px; border: 1px solid transparent; }
  .match-row .left { flex: 1; font-family: Georgia, serif; }
  .match-row select { max-width: 56%; }
  .match-row.correct { border-color: var(--ready); background: color-mix(in srgb, var(--ready) 16%, transparent); }
  .match-row.wrong { border-color: var(--err); background: color-mix(in srgb, var(--err) 14%, transparent); }
  .check { align-self: flex-start; margin-top: 2px; }
  .ans { display: block; font-size: 12.5px; }

  .recall { width: 100%; resize: vertical; font: inherit; font-family: Georgia, serif;
    background: var(--bg); color: var(--ink); border: 1px solid var(--border); border-radius: 9px; padding: 9px 11px; }
  .reveal { align-self: flex-start; }
  .ideal { font-family: Georgia, serif; line-height: 1.5;
    background: color-mix(in srgb, var(--accent) 9%, transparent);
    border-left: 3px solid var(--accent); border-radius: 0 8px 8px 0; padding: 9px 12px; }
  .why { font-size: 13px; color: var(--meta); line-height: 1.45; }
  .why.ok { color: color-mix(in srgb, var(--ready) 80%, var(--fg)); }
  .rate { display: flex; align-items: center; gap: 8px; font-size: 13px; flex-wrap: wrap; }
  .rate button.on { background: var(--accent); color: var(--accent-ink); }

  footer { display: flex; align-items: center; gap: 10px; padding: 11px 16px; border-top: 1px solid var(--border); }
  .ghost { background: transparent; color: var(--meta); font-size: 12px; padding: 6px 8px; }

  @media (max-width: 560px) { .quizwin { right: 50%; transform: translate(50%, -50%); width: 94vw; } }
</style>
