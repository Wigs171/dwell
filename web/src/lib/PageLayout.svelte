<script lang="ts">
  // Renders a page's body text + 1–3 figures in one of the named layouts
  // (see layouts.css). Reusable: the lab feeds it mock data today; the real
  // Reader will feed it a page's text + resolved vault images later.
  import './layouts.css';
  import type { PageImage, LayoutId } from './types';

  let { layout, text, images = [] }: {
    layout: LayoutId;
    text: string;
    images?: PageImage[];
  } = $props();

  // Body text → paragraphs (the real engine emits \n\n-separated prose).
  const paras = $derived(text.split(/\n{2,}/).map((s) => s.trim()).filter(Boolean));
  const img = (i: number): PageImage | undefined => images[i];

  // Where a mid-flow float drops in (diagonal's 2nd image, mosaic's 3rd):
  // after the 2nd paragraph, clamped so short pages still place it.
  const midAt = $derived(Math.min(2, Math.max(0, paras.length - 1)));
  // Magazine's centered feature image drops in around the vertical middle.
  const midSpan = $derived(Math.max(1, Math.round(paras.length / 2)));
</script>

{#snippet figEl(im: PageImage | undefined, cls: string)}
  {#if im}
    <figure class={cls}>
      <div class="frame"><img src={im.src} alt={im.alt} loading="lazy" /></div>
      {#if im.caption}<figcaption>{im.caption}</figcaption>{/if}
    </figure>
  {/if}
{/snippet}

<div class="page-layout l-{layout}">
  {#if layout === 'top'}
    {@render figEl(img(0), 'fig-top')}
    {#each paras as p}<p>{p}</p>{/each}

  {:else if layout === 'bottom'}
    <div class="body">{#each paras as p}<p>{p}</p>{/each}</div>
    {@render figEl(img(0), 'fig-bottom')}

  {:else if layout === 'side'}
    {@render figEl(img(0), 'fig-side')}
    {#each paras as p}<p>{p}</p>{/each}

  {:else if layout === 'inset'}
    {@render figEl(img(0), 'fig-inset')}
    {#each paras as p}<p>{p}</p>{/each}

  {:else if layout === 'diagonal'}
    {@render figEl(img(0), 'fig-a')}
    {#each paras as p, i}
      {#if i === midAt}{@render figEl(img(1), 'fig-b')}{/if}
      <p>{p}</p>
    {/each}

  {:else if layout === 'magazine'}
    <div class="mag-cols">
      {#each paras as p, i}
        {#if i === midSpan}{@render figEl(img(0), 'mag-mid')}{/if}
        <p>{p}</p>
      {/each}
    </div>

  {:else if layout === 'rail'}
    <figure class="rail-fig">
      {#if img(0)}
        <div class="frame"><img src={img(0)!.src} alt={img(0)!.alt} loading="lazy" /></div>
        {#if img(0)!.caption}<figcaption>{img(0)!.caption}</figcaption>{/if}
      {/if}
    </figure>
    <div class="rail-text">{#each paras as p}<p>{p}</p>{/each}</div>

  {:else if layout === 'mosaic'}
    {@render figEl(img(0), 'banner')}
    <div class="mbody">
      {@render figEl(img(1), 'fig-2')}
      {#each paras as p, i}
        {#if i === midAt}{@render figEl(img(2), 'fig-3')}{/if}
        <p>{p}</p>
      {/each}
    </div>

  {:else if layout === 'hero'}
    {#if img(0)}<img class="hero-img" src={img(0)!.src} alt={img(0)!.alt} loading="lazy" />{/if}
    <div class="hero-scrim"></div>
    <div class="hero-body">
      {#each paras.slice(0, 3) as p}<p>{p}</p>{/each}
      {#if img(0)?.caption}<div class="hero-cap">{img(0)!.caption}</div>{/if}
    </div>
  {/if}
</div>
