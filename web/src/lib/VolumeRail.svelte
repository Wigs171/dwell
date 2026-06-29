<script lang="ts">
  // A vertical narration-volume slider that floats over the reading stage — the
  // quick, touch-first volume control for tablets (the Settings slider stays too).
  // Touch events are kept off the stage so dragging the rail never swipes the page.
  import { dwell } from './dwell.svelte';

  let lastVol = $state(1);                    // restore level when un-muting
  const pct = $derived(Math.round(dwell.ttsVolume * 100));

  function toggleMute() {
    if (dwell.ttsVolume > 0) { lastVol = dwell.ttsVolume; dwell.setTtsVolume(0); }
    else dwell.setTtsVolume(lastVol > 0 ? lastVol : 1);
  }
  const stop = (e: Event) => e.stopPropagation();
</script>

<div class="volrail" role="group" aria-label="Narration volume"
     ontouchstart={stop} ontouchmove={stop} ontouchend={stop} onwheel={stop}
     title="Narration volume">
  <div class="track">
    <input class="vol" type="range" min="0" max="1" step="0.05" value={dwell.ttsVolume}
           style="--pct:{pct}%"
           oninput={(e) => dwell.setTtsVolume(+e.currentTarget.value)}
           aria-label="Narration volume" aria-valuetext="{pct}%" />
  </div>
  <button class="icon" onclick={toggleMute}
          title={dwell.ttsVolume === 0 ? 'Unmute narration' : 'Mute narration'}>
    {dwell.ttsVolume === 0 ? '🔇' : '🔊'}
  </button>
</div>

<style>
  .volrail {
    position: absolute; right: 14px; bottom: 54px; z-index: 21;
    display: flex; flex-direction: column; align-items: center; gap: 6px;
    padding: 14px 9px 8px;
    background: color-mix(in srgb, var(--panel) 82%, transparent);
    border: 1px solid var(--border); border-radius: 14px;
    box-shadow: 0 4px 16px #0006;
    touch-action: none;                       /* the slider owns the gesture, not the deck */
  }
  /* wrapper sized to the ROTATED input (154 long × ~26 thumb), so layout reserves a
     tall, narrow column. The native range is rotated −90° → a vertical slider whose
     hit-testing + touch tracking the browser handles correctly (works on iPad Safari). */
  .track { width: 30px; height: 124px; display: flex; align-items: center; justify-content: center; }
  .vol {
    -webkit-appearance: none; appearance: none;
    width: 120px; height: 7px; margin: 0; border-radius: 5px;
    transform: rotate(-90deg); cursor: pointer; touch-action: none;
    /* fill from the low end (= bottom after rotation) up to the current level */
    background: linear-gradient(to right,
      var(--accent) 0 var(--pct),
      color-mix(in srgb, var(--fg) 18%, transparent) var(--pct) 100%);
  }
  .vol::-webkit-slider-thumb {
    -webkit-appearance: none; appearance: none;
    width: 26px; height: 26px; border-radius: 50%;
    background: var(--accent); border: 2px solid var(--pane); box-shadow: 0 1px 5px #0008;
  }
  .vol::-moz-range-thumb {
    width: 26px; height: 26px; border-radius: 50%;
    background: var(--accent); border: 2px solid var(--pane); box-shadow: 0 1px 5px #0008;
  }
  .vol::-moz-range-track { background: transparent; }
  .icon {
    background: none; border: none; padding: 4px 6px; border-radius: 8px;
    font-size: 17px; line-height: 1; cursor: pointer;
  }
  .icon:hover { background: var(--hover); }
</style>
