<script lang="ts">
  // Renders a page's body prose + ONE derived text-figure in the right slot
  // (see textfigures.css). The text edition of PageLayout.svelte: the lab feeds
  // it the Pythagoras prose + hand-authored payloads today; the real Reader will
  // feed it page.text + figures the engine derives later.
  //
  // INVARIANT: every figure is a <figure data-narration="skip"> (or carries the
  // attribute) so it stays OUT of the karaoke/clarify offset walk. The body text
  // is never reshuffled — variety comes from the discrete figure, never the
  // reading column. Drop-cap/raised-initial are pure ::first-letter (no node).
  import './textfigures.css';
  import type { TextFigureId, TextFigureData } from './types';

  let { figure, text, data = {} }: {
    figure: TextFigureId;
    text: string;
    data?: TextFigureData;
  } = $props();

  const paras = $derived(text.split(/\n{2,}/).map((s) => s.trim()).filter(Boolean));
  // Where an in-flow figure drops in: after the 2nd paragraph, clamped.
  const midAt = $derived(Math.min(2, Math.max(0, paras.length - 1)));

  // Float-wrap figures reuse the image `side` slot: the figure floats to the
  // top-right and the body text WRAPS around it (a text-figure standing in for
  // an image). Placed DOM-adjacent (before the text it wraps).
  // Body modifier for the pure-CSS opening initials (no <figure>, no node).
  const bodyMod = $derived(figure === 'drop-cap' ? 'dropcap' : figure === 'raised-initial' ? 'raised' : '');

  const CALLOUT_IC: Record<string, string> = {
    note: 'ℹ', tip: '💡', 'key-insight': '🔑', question: '❓', caution: '⚠', quote: '❝',
  };
  const calloutLabel = (k: string, override?: string) =>
    override ?? (k === 'key-insight' ? 'Key insight' : k.charAt(0).toUpperCase() + k.slice(1));

  // Split body around an anchor index, for lane figures placed DOM-adjacent.
  const anchor = $derived(
    figure === 'sidenote' ? (data.sidenote?.afterPara ?? 1)
    : figure === 'pull-quote' ? 1   // adjacent to its source line (NN/g)
    : -1,
  );
</script>

<!-- page modifier is namespaced `tff-` so it can never collide with a device
     class (e.g. figure="callout" would otherwise match the inner .tf-callout box) -->
<div class="tf-page tff-{figure}">
  {#if figure === 'kicker'}
    <figure class="tf-kicker" data-narration="skip">{data.kicker ?? 'SECTION'}</figure>
    {#each paras as p}<p>{p}</p>{/each}

  {:else if figure === 'headline-stack'}
    <figure class="tf-headline" data-narration="skip">
      {#if data.kicker}<div class="tf-kicker">{data.kicker}</div>{/if}
      <div class="tf-title">{data.title ?? 'Untitled'}</div>
      {#if data.deck}<p class="tf-deck">{data.deck}</p>{/if}
    </figure>
    {#each paras as p}<p>{p}</p>{/each}

  {:else if figure === 'deck'}
    <figure class="tf-deck-solo" data-narration="skip">{data.deck ?? ''}</figure>
    {#each paras as p}<p>{p}</p>{/each}

  {:else if figure === 'tldr'}
    <figure class="tf-tldr" data-narration="skip"><span class="tag">TL;DR</span><p>{data.tldr ?? ''}</p></figure>
    {#each paras as p}<p>{p}</p>{/each}

  {:else if figure === 'key-takeaways'}
    <figure class="tf-takeaways" data-narration="skip">
      <div class="tf-h">Key takeaways</div>
      <ul>{#each data.takeaways ?? [] as t}<li>{t}</li>{/each}</ul>
    </figure>
    {#each paras as p}<p>{p}</p>{/each}

  {:else if figure === 'callout'}
    {@const c = data.callout}
    {#each paras as p, i}
      <p>{p}</p>
      {#if i === midAt && c}
        <figure class="tf-callout" data-kind={c.kind} data-narration="skip">
          <div class="ic" aria-hidden="true">{CALLOUT_IC[c.kind] ?? 'ℹ'}</div>
          <div class="ct">
            <div class="cl">{calloutLabel(c.kind, c.label)}</div>
            <p>{c.text}</p>
          </div>
        </figure>
      {/if}
    {/each}

  {:else if figure === 'pull-quote'}
    {#each paras as p, i}
      {#if i === anchor && data.quote}
        <!-- floats top-right beside its source line; text wraps. Duplicates a body
             line → aria-hidden (out of the TTS walk already). -->
        <figure class="tf-pullquote tf-float" data-narration="skip" aria-hidden="true">{data.quote.text}</figure>
      {/if}
      <p>{p}</p>
    {/each}

  {:else if figure === 'block-quote'}
    {#each paras as p, i}
      <p>{p}</p>
      {#if i === midAt && data.quote}
        <figure class="tf-blockquote" data-narration="skip">
          <p>{data.quote.text}</p>
          {#if data.quote.cite}<cite>{data.quote.cite}</cite>{/if}
        </figure>
      {/if}
    {/each}

  {:else if figure === 'stepped-list'}
    {#each paras.slice(0, midAt + 1) as p}<p>{p}</p>{/each}
    <figure class="tf-steps" data-narration="skip">
      <div class="tf-h">In sequence</div>
      <ol>{#each data.steps ?? [] as s}<li>{s}</li>{/each}</ol>
    </figure>
    {#each paras.slice(midAt + 1) as p}<p>{p}</p>{/each}

  {:else if figure === 'comparison'}
    {@const cmp = data.comparison}
    {#each paras.slice(0, midAt + 1) as p}<p>{p}</p>{/each}
    {#if cmp}
      <figure class="tf-compare" data-narration="skip">
        <div class="col"><div class="ch">{cmp.aTitle}</div><p>{cmp.a}</p></div>
        <div class="col"><div class="ch">{cmp.bTitle}</div><p>{cmp.b}</p></div>
      </figure>
    {/if}
    {#each paras.slice(midAt + 1) as p}<p>{p}</p>{/each}

  {:else if figure === 'accordion'}
    {@const ac = data.accordion}
    {#each paras as p, i}
      <p>{p}</p>
      {#if i === midAt && ac}
        <figure data-narration="skip">
          <details class="tf-accordion">
            <summary>{ac.summary}</summary>
            <div class="det">{ac.detail}</div>
          </details>
        </figure>
      {/if}
    {/each}

  {:else if figure === 'read-time'}
    {@const rt = data.readTime}
    <figure class="tf-readtime" data-narration="skip">
      <span>≈ {rt?.mins ?? 3} min · narrated</span>
      <span class="bar"><i style="width:{Math.round((rt?.progress ?? 0) * 100)}%"></i></span>
      <span>{Math.round((rt?.progress ?? 0) * 100)}%</span>
    </figure>
    {#each paras as p}<p>{p}</p>{/each}

  {:else if figure === 'sidenote'}
    {@const sn = data.sidenote}
    {#each paras as p, i}
      {#if i === anchor && sn}
        <figure class="tf-sidenote tf-float" data-narration="skip"><span class="mk">{sn.marker}</span> {sn.text}</figure>
      {/if}
      <p>{p}{#if i === anchor && sn}<sup class="tf-ref">{sn.marker}</sup>{/if}</p>
    {/each}

  {:else if figure === 'drop-cap' || figure === 'raised-initial'}
    <div class="tf-body {bodyMod}">{#each paras as p}<p>{p}</p>{/each}</div>

  {:else if figure === 'big-number'}
    {@const bn = data.bigNumber}
    {#if bn}
      <figure class="tf-bignum" data-narration="skip">
        <div class="n">{bn.value}</div>
        <div class="lbl">{bn.label}</div>
      </figure>
    {/if}
    {#each paras as p}<p>{p}</p>{/each}

  {:else if figure === 'see-also'}
    <!-- floats top-right; the whole text block runs down the left beside it -->
    <figure class="tf-seealso tf-float" data-narration="skip">
      <div class="tf-h">See also</div>
      <ul>
        {#each data.seeAlso ?? [] as s}
          <li><a href={'#'} onclick={(e) => e.preventDefault()}>{s.title}</a>{#if s.note}<span class="note">{s.note}</span>{/if}</li>
        {/each}
      </ul>
    </figure>
    {#each paras as p}<p>{p}</p>{/each}

  {:else if figure === 'glossary'}
    <figure class="tf-glossary tf-float" data-narration="skip">
      <div class="tf-h">Glossary</div>
      <dl>{#each data.glossary ?? [] as g}<dt>{g.term}</dt><dd>{g.def}</dd>{/each}</dl>
    </figure>
    {#each paras as p}<p>{p}</p>{/each}

  {:else if figure === 'source-strip'}
    {#each paras as p}<p>{p}</p>{/each}
    {@const s = data.sources}
    <figure class="tf-sources" data-narration="skip">
      <span class="pill">📚 synthesized from {s?.count ?? 0} sources</span>
      {#if s?.grounded}<span class="pill ok">✓ grounded</span>{/if}
    </figure>

  {:else if figure === 'definition'}
    {@const df = data.definition}
    {#each paras as p, i}
      {#if i === (df?.afterPara ?? 0) && df}
        {@const parts = p.split(df.term)}
        <p>{parts[0]}<span class="tf-def"><span class="term" tabindex="0" role="button" aria-describedby="tf-gloss">{df.term}</span><span class="gloss" role="tooltip" id="tf-gloss" data-narration="skip">{df.def}</span></span>{parts.slice(1).join(df.term)}</p>
      {:else}
        <p>{p}</p>
      {/if}
    {/each}

  {:else if figure === 'timeline'}
    <figure class="tf-timeline" data-narration="skip">
      {#each data.timeline ?? [] as ev}
        <div class="ev"><div class="when">{ev.when}</div><div class="what">{ev.what}</div></div>
      {/each}
    </figure>
    {#each paras as p}<p>{p}</p>{/each}
  {/if}
</div>
