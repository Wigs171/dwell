<script lang="ts">
  // "My voices" — the cloud narration library (fal.ai Qwen3-TTS studio lane).
  // Clone from a scripted recording or an uploaded clip (one-time enrollment;
  // the embedding lives on this machine), or design a voice from a text
  // description. Enrolled voices appear in the narrator picker as `cloud:slug`.
  import { dwell } from './dwell.svelte';

  type CloudVoice = { slug: string; name: string; kind: 'clone' | 'design'; created?: string };
  type CloudModel = { id: string; label: string; presets: string[]; per_1k: number; page_usd: number; speed: string };

  // Scripted passage: because WE choose the words, enrollment gets a perfect
  // reference transcript for free (ICL cloning quality needs it).
  const PASSAGE =
    'Well now, let me tell you how I like to read. Some days I take it slow, ' +
    'like a river with nowhere to be, and some days I want the whole story at ' +
    'once. Either way, the words ought to sound like me. The highs, the lows, ' +
    'and the little pauses in between. That is the voice I want reading my pages.';

  let cloudOk = $state(false);
  let cloudErr = $state('');
  let voices = $state<CloudVoice[]>([]);
  let models = $state<CloudModel[]>([]);
  let selModel = $state('');
  let selPreset = $state('');
  const curModel = $derived(models.find((m) => m.id === selModel));
  let mode = $state<'' | 'record' | 'upload' | 'design'>('');
  let err = $state('');
  let busySlug = $state('');          // slug being previewed
  let enrolling = $state(false);
  let keyInput = $state('');
  let editingKey = $state(false);

  let vname = $state('');
  let consent = $state(false);
  let transcript = $state('');
  let upFile: File | null = $state(null);
  let designDesc = $state('');
  type CloneEngine = { id: string; label: string; note: string; per_1k: number; page_usd: number; speed: string };
  let engines = $state<CloneEngine[]>([]);
  let engine = $state('qwen');
  const curEngine = $derived(engines.find((e) => e.id === engine));

  let recording = $state(false);
  let recSecs = $state(0);
  let recBlob: Blob | null = $state(null);
  let mediaStream: MediaStream | null = null;
  let audioCtx: AudioContext | null = null;
  let proc: ScriptProcessorNode | null = null;
  let pcm: Float32Array[] = [];
  let recRate = 24000;
  let timer: ReturnType<typeof setInterval> | null = null;

  async function refresh() {
    try {
      const r = await fetch('/tts/library');
      const j = await r.json();
      cloudOk = !!j.available;
      cloudErr = j.error || '';
      voices = j.voices || [];
      models = j.models || [];
      engines = j.clone_engines || [];
      if (!selModel && models.length) { selModel = models[0].id; selPreset = models[0].presets[0] || ''; }
    } catch { cloudOk = false; }
  }
  refresh();

  function reset() {
    mode = ''; err = ''; vname = ''; consent = false; transcript = '';
    upFile = null; designDesc = ''; recBlob = null; recSecs = 0;
  }

  async function saveKey() {
    if (!keyInput.trim()) return;
    await dwell.setTtsKey(keyInput.trim());   // stored server-side; reloads voices
    keyInput = ''; editingKey = false;
    await refresh();
  }
  async function removeKey() { await dwell.clearTtsKey(); await refresh(); }

  // ---- recording (raw PCM via script processor → 16-bit WAV; codec-free) ----
  async function startRec() {
    err = ''; recBlob = null; pcm = [];
    try {
      mediaStream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
    } catch { err = 'microphone permission denied'; return; }
    audioCtx = new AudioContext();
    recRate = audioCtx.sampleRate;
    const src = audioCtx.createMediaStreamSource(mediaStream);
    proc = audioCtx.createScriptProcessor(4096, 1, 1);
    proc.onaudioprocess = (e) => { pcm.push(new Float32Array(e.inputBuffer.getChannelData(0))); };
    const mute = audioCtx.createGain();
    mute.gain.value = 0;                       // keep the graph alive without echo
    src.connect(proc); proc.connect(mute); mute.connect(audioCtx.destination);
    recording = true; recSecs = 0;
    timer = setInterval(() => { recSecs += 1; if (recSecs >= 25) stopRec(); }, 1000);
  }

  function stopRec() {
    if (timer) { clearInterval(timer); timer = null; }
    proc?.disconnect(); proc = null;
    mediaStream?.getTracks().forEach((t) => t.stop()); mediaStream = null;
    void audioCtx?.close(); audioCtx = null;
    recording = false;
    const n = pcm.reduce((a, c) => a + c.length, 0);
    if (n < recRate * 3) { err = 'too short — read the whole passage (~15s)'; return; }
    recBlob = encodeWav(pcm, recRate);
  }

  function encodeWav(chunks: Float32Array[], rate: number): Blob {
    const n = chunks.reduce((a, c) => a + c.length, 0);
    const buf = new ArrayBuffer(44 + n * 2);
    const v = new DataView(buf);
    const w = (o: number, s: string) => { for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
    w(0, 'RIFF'); v.setUint32(4, 36 + n * 2, true); w(8, 'WAVE'); w(12, 'fmt ');
    v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
    v.setUint32(24, rate, true); v.setUint32(28, rate * 2, true);
    v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, 'data');
    v.setUint32(40, n * 2, true);
    let o = 44;
    for (const c of chunks) for (let i = 0; i < c.length; i++, o += 2) {
      const s = Math.max(-1, Math.min(1, c[i]));
      v.setInt16(o, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return new Blob([buf], { type: 'audio/wav' });
  }

  // ---- enrollment -----------------------------------------------------------
  async function enrollClone() {
    const audio = mode === 'record' ? recBlob : upFile;
    if (!audio || !vname.trim() || !consent) return;
    enrolling = true; err = '';
    try {
      const fd = new FormData();
      fd.append('name', vname.trim());
      fd.append('transcript', mode === 'record' ? PASSAGE : transcript.trim());
      fd.append('consent', 'true');
      fd.append('engine', engine);
      fd.append('audio', audio, mode === 'record' ? 'recording.wav' : (upFile?.name || 'sample.wav'));
      const r = await fetch('/tts/library/clone', { method: 'POST', body: fd });
      if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
      const id = 'cloud:' + (await r.json()).voice.slug;
      adopt(id); reset(); await refresh();
    } catch (e) { err = String((e as Error).message || e); }
    enrolling = false;
  }

  async function enrollDesign() {
    if (!vname.trim() || !designDesc.trim()) return;
    enrolling = true; err = '';
    try {
      const r = await fetch('/tts/library/design', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: vname.trim(), description: designDesc.trim() }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
      const id = 'cloud:' + (await r.json()).voice.slug;
      adopt(id); reset(); await refresh();
    } catch (e) { err = String((e as Error).message || e); }
    enrolling = false;
  }

  function adopt(id: string) {
    if (!dwell.ttsVoices.includes(id)) dwell.ttsVoices = [id, ...dwell.ttsVoices];
    dwell.setTtsVoice(id);
  }

  // ---- per-voice actions ------------------------------------------------------
  // `slug` is either an enrolled library slug or a "<model>/<preset>" id.
  async function preview(slug: string) {
    busySlug = slug; err = '';
    try {
      const r = await fetch('/tts/library/preview', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ voice: 'cloud:' + slug }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
      const a = new Audio('data:audio/mpeg;base64,' + (await r.json()).b64);
      a.volume = dwell.ttsVolume;
      void a.play();
    } catch (e) { err = String((e as Error).message || e); }
    busySlug = '';
  }

  async function remove(slug: string) {
    await fetch('/tts/library/' + slug, { method: 'DELETE' });
    const id = 'cloud:' + slug;
    dwell.ttsVoices = dwell.ttsVoices.filter((x) => x !== id);
    if (dwell.ttsVoice === id) dwell.setTtsVoice(dwell.ttsVoices.find((x) => !x.startsWith('cloud:')) || 'af_heart');
    await refresh();
  }
</script>

<div class="vlib">
  <span class="vl-h">My voices <span class="note">· cloud studio (fal.ai)</span></span>
  <div class="vl-key">
    {#if dwell.ttsHasKey && !editingKey}
      <span class="vl-lab">fal.ai key</span>
      <span class="ep-models">set ✓</span>
      <button class="ep-act" title="change" onclick={() => { editingKey = true; keyInput = ''; }}>change</button>
      <button class="ep-act rm" title="remove" onclick={removeKey}>✕</button>
    {:else}
      <input class="ep-in" type="password" placeholder="fal.ai API key" bind:value={keyInput} />
      <button class="add" disabled={!keyInput.trim()} onclick={saveKey}>Save</button>
      {#if editingKey}<button class="ep-act" onclick={() => { editingKey = false; }}>cancel</button>{/if}
    {/if}
  </div>
  <p class="hint">Stored locally; never shown again. Get a key at <code>fal.ai/dashboard/keys</code>. Falls back to <code>FALAI_API_KEY</code> in <code>.env</code> if unset.</p>
  {#if !cloudOk}
    <p class="hint">{cloudErr || 'Add a fal.ai key above to clone, design, or use preset voices.'}</p>
  {:else}
    {#each voices as v (v.slug)}
      <div class="vl-row">
        <span class="vl-kind">{v.kind === 'clone' ? '🎙' : '✏'}</span>
        <span class="vl-name" title={v.slug}>{v.name}</span>
        <button class="ep-act" title="preview (~15s to synthesize)" disabled={busySlug === v.slug}
                onclick={() => preview(v.slug)}>{busySlug === v.slug ? '…' : '▶'}</button>
        <button class="ep-act" title="narrate with this voice" onclick={() => adopt('cloud:' + v.slug)}
                >{dwell.ttsVoice === 'cloud:' + v.slug ? '◉' : '○'}</button>
        <button class="ep-act rm" title="delete" onclick={() => remove(v.slug)}>✕</button>
      </div>
    {/each}

    {#if models.length}
      <div class="vl-presets">
        <span class="vl-h2">Preset voices</span>
        <div class="vl-pick">
          <select bind:value={selModel}
                  onchange={() => { selPreset = curModel?.presets[0] || ''; }}>
            {#each models as m (m.id)}<option value={m.id}>{m.label}</option>{/each}
          </select>
          <select bind:value={selPreset}>
            {#each curModel?.presets || [] as p (p)}<option value={p}>{p}</option>{/each}
          </select>
          <button class="ep-act" title="preview" disabled={busySlug === selModel + '/' + selPreset}
                  onclick={() => preview(selModel + '/' + selPreset)}
                  >{busySlug === selModel + '/' + selPreset ? '…' : '▶'}</button>
          <button class="ep-act" title="narrate with this voice"
                  onclick={() => adopt('cloud:' + selModel + '/' + selPreset)}
                  >{dwell.ttsVoice === 'cloud:' + selModel + '/' + selPreset ? '◉' : '○'}</button>
        </div>
        {#if curModel}
          <p class="hint">≈ ${curModel.page_usd.toFixed(2)}/page · {curModel.speed} —
            {curModel.speed.includes('LIVE') ? 'keeps up with narration.' : 'audio trails the page; best for auto-flow or exports.'}
            (Kokoro voices above: free · instant · local.)</p>
        {/if}
      </div>
    {/if}

    {#if !mode}
      <div class="vl-add">
        <button class="add" onclick={() => { reset(); mode = 'record'; }}>🎙 Record</button>
        <button class="add" onclick={() => { reset(); mode = 'upload'; }}>📁 Upload</button>
        <button class="add" onclick={() => { reset(); mode = 'design'; }}>✏ Design</button>
      </div>
    {:else}
      <div class="vl-form">
        {#if mode === 'record'}
          <p class="vl-passage">“{PASSAGE}”</p>
          {#if !recording && !recBlob}
            <button class="add" onclick={startRec}>● Start recording — read the passage above</button>
          {:else if recording}
            <button class="add" onclick={stopRec}>■ Stop ({recSecs}s)</button>
          {:else}
            <span class="note">✓ recorded {recSecs}s · <button class="ep-act" onclick={startRec}>redo</button></span>
          {/if}
        {:else if mode === 'upload'}
          <input type="file" accept="audio/*,.wav,.mp3,.m4a,.ogg"
                 onchange={(e) => { upFile = e.currentTarget.files?.[0] || null; }} />
          <input class="ep-in" placeholder="what is said in the clip (optional, improves quality)" bind:value={transcript} />
        {:else}
          <textarea class="ep-in vl-desc" rows="3" bind:value={designDesc}
                    placeholder="Describe the voice — e.g. “an extremely thick Cajun Louisiana accent, warm heavy Southern drawl, an old front-porch storyteller”"></textarea>
        {/if}
        <input class="ep-in" placeholder="voice name" bind:value={vname} />
        {#if mode !== 'design'}
          {#if engines.length}
            <div class="vl-pick">
              <span class="vl-lab">Clone with</span>
              <select bind:value={engine}>
                {#each engines as e (e.id)}<option value={e.id}>{e.label} — {e.note}</option>{/each}
              </select>
            </div>
            {#if curEngine}<p class="hint">≈ ${curEngine.page_usd.toFixed(2)}/page · {curEngine.speed}</p>{/if}
          {/if}
          <label class="chk"><input type="checkbox" bind:checked={consent} />
            This is my voice, or one I have permission to use</label>
          <p class="hint">The sample is sent once to fal.ai for enrollment; narration sends your page text and the voice.</p>
        {/if}
        <div class="vl-add">
          <button class="add" disabled={enrolling || !vname.trim() || (mode === 'design' ? !designDesc.trim() : (!consent || (mode === 'record' ? !recBlob : !upFile)))}
                  onclick={() => (mode === 'design' ? enrollDesign() : enrollClone())}>
            {enrolling ? 'Enrolling…' : 'Save voice'}</button>
          <button class="ep-act" onclick={reset}>cancel</button>
        </div>
      </div>
    {/if}
    {#if err}<p class="vl-err">{err}</p>{/if}
  {/if}
</div>

<style>
  .vlib { margin-top: 0.6rem; padding-top: 0.5rem; border-top: 1px solid color-mix(in srgb, currentColor 14%, transparent); }
  .vl-h { font-size: 0.78rem; opacity: 0.85; display: block; margin-bottom: 0.3rem; }
  .vl-h .note, .note { opacity: 0.55; font-size: 0.72rem; }
  .vl-row { display: flex; align-items: center; gap: 0.4rem; padding: 0.15rem 0; }
  .vl-kind { font-size: 0.8rem; }
  .vl-name { flex: 1; font-size: 0.82rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .vl-add { display: flex; gap: 0.4rem; margin-top: 0.35rem; }
  .vl-presets { margin-top: 0.5rem; }
  .vl-h2 { font-size: 0.74rem; opacity: 0.7; display: block; margin-bottom: 0.25rem; }
  .vl-pick { display: flex; gap: 0.35rem; align-items: center; }
  .vl-pick select { min-width: 0; flex: 1; }
  .vl-lab { font-size: 0.76rem; opacity: 0.7; white-space: nowrap; }
  .vl-key { display: flex; gap: 0.4rem; align-items: center; margin: 0.15rem 0; }
  .vl-key .ep-in { flex: 1; min-width: 0; }
  .vl-form { display: flex; flex-direction: column; gap: 0.4rem; margin-top: 0.35rem; }
  .vl-passage { font-size: 0.78rem; font-style: italic; opacity: 0.8; margin: 0; line-height: 1.45; }
  .vl-desc { resize: vertical; }
  .vl-err { color: #d66; font-size: 0.75rem; margin: 0.2rem 0 0; }
</style>
