// Typed client for the Dwell FastAPI server. Paths are root-relative; in dev Vite
// proxies them to the engine (see vite.config.ts), in prod FastAPI serves both.
import type {
  Branch, ExpandDone, MissedPair, PageDone, PageFrame, PageStart, QuizQuestion, SessionInfo, VaultInfo,
} from './types';

async function jget<T>(path: string): Promise<T> {
  const r = await fetch(path);
  if (!r.ok) throw new Error(await detail(r));
  return r.json() as Promise<T>;
}
async function jpost<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await detail(r));
  return r.json() as Promise<T>;
}
async function detail(r: Response): Promise<string> {
  try { return (await r.json()).detail || r.statusText; } catch { return r.statusText; }
}

// ---- streaming (SSE-over-fetch) -------------------------------------------
// sse_starlette emits CRLF and every event's `data` is a JSON object; split
// events on a blank line that may use CRLF or LF, strip \r per field line.
export interface SSEHandlers<Start, Done> {
  start?: (p: Start) => void;
  frame?: (p: PageFrame) => void;
  done?: (p: Done) => void;
  error?: (p: { message: string }) => void;
}

async function streamPost<Start, Done>(
  path: string, body: unknown, h: SSEHandlers<Start, Done>, signal?: AbortSignal,
): Promise<void> {
  const r = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body), signal,
  });
  if (!r.ok) throw new Error(await detail(r));
  if (!r.body) throw new Error('no response body');
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  const BOUND = /\r?\n\r?\n/;
  let buf = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let m: RegExpExecArray | null;
    while ((m = BOUND.exec(buf))) {
      const raw = buf.slice(0, m.index);
      buf = buf.slice(m.index + m[0].length);
      let ev = 'message';
      let data = '';
      for (const line of raw.split(/\r?\n/)) {
        if (line.startsWith('event:')) ev = line.slice(6).trim();
        else if (line.startsWith('data:')) data += (data ? '\n' : '') + line.slice(5).replace(/^ /, '');
      }
      if (!data) continue;
      let payload: unknown;
      try { payload = JSON.parse(data); } catch { continue; }
      const fn = (h as Record<string, ((p: unknown) => void) | undefined>)[ev];
      if (fn) fn(payload);
    }
  }
}

// Generic SSE consumer (event name → handler). Same wire format as streamPost.
async function streamEvents(
  path: string, body: unknown, handlers: Record<string, (p: any) => void>, signal?: AbortSignal,
): Promise<void> {
  const r = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal,
  });
  if (!r.ok) throw new Error(await detail(r));
  if (!r.body) throw new Error('no response body');
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  const BOUND = /\r?\n\r?\n/;
  let buf = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let m: RegExpExecArray | null;
    while ((m = BOUND.exec(buf))) {
      const raw = buf.slice(0, m.index);
      buf = buf.slice(m.index + m[0].length);
      let ev = 'message';
      let data = '';
      for (const line of raw.split(/\r?\n/)) {
        if (line.startsWith('event:')) ev = line.slice(6).trim();
        else if (line.startsWith('data:')) data += (data ? '\n' : '') + line.slice(5).replace(/^ /, '');
      }
      if (!data) continue;
      let payload: unknown;
      try { payload = JSON.parse(data); } catch { continue; }
      handlers[ev]?.(payload);
    }
  }
}

// ---- endpoints -------------------------------------------------------------
export const api = {
  health: () => jget<{ ok: boolean; sessions: number; vault_root: string }>('/health'),
  vaults: () => jget<{ root: string; vaults: VaultInfo[] }>('/vaults'),
  vaultCoverUrl: (vaultPath: string) => `/vault-cover?vault=${encodeURIComponent(vaultPath)}`,
  closeSession: (sid: string) =>
    fetch(`/session?session=${encodeURIComponent(sid)}`, { method: 'DELETE' }).catch(() => {}),

  vaultSources: (path: string) =>
    jget<{ sources: import('./types').VaultSource[] }>(`/vault-sources?vault=${encodeURIComponent(path)}`),
  vaultImport: (path: string) =>
    jpost<{ ok: boolean; vault: VaultInfo }>('/vault/import', { path }),
  vaultDelete: async (path: string, purge: boolean) => {
    const r = await fetch(`/vault?vault=${encodeURIComponent(path)}&purge=${purge}`, { method: 'DELETE' });
    if (!r.ok) throw new Error(await detail(r));
    return r.json() as Promise<{ ok: boolean }>;
  },
  vaultSetCover: async (path: string, file: File) => {
    const fd = new FormData();
    fd.append('vault', path);
    fd.append('file', file);
    const r = await fetch('/vault/cover', { method: 'POST', body: fd });
    if (!r.ok) throw new Error(await detail(r));
    return r.json() as Promise<{ ok: boolean; has_cover: boolean }>;
  },
  vaultRemoveCover: async (path: string) => {
    const r = await fetch(`/vault/cover?vault=${encodeURIComponent(path)}`, { method: 'DELETE' });
    if (!r.ok) throw new Error(await detail(r));
    return r.json() as Promise<{ ok: boolean; has_cover: boolean }>;
  },

  // ---- Learn (vault builder) ----
  learnCreate: (name: string, topic: string) =>
    jpost<{ vault: string; name: string; sources: import('./types').LearnSources }>('/learn/create', { name, topic }),
  learnOpen: (vault: string) =>
    jpost<{ vault: string; name: string; sources: import('./types').LearnSources }>('/learn/open', { vault }),
  learnBuild: (vault: string, dry: boolean, handlers: Record<string, (p: any) => void>, signal?: AbortSignal, resume = false, opts: Record<string, unknown> = {}) =>
    streamEvents('/learn/build', { vault, dry, resume, ...opts }, handlers, signal),
  learnBuildStop: (vault: string) =>
    jpost<{ ok: boolean }>('/learn/build/stop', { vault }),

  // ---- Models & Keys (multi-provider endpoints) ----
  endpoints: () => jget<{ endpoints: import('./types').Endpoint[] }>('/endpoints'),
  endpointAdd: (name: string, base_url: string, api_key: string) =>
    jpost<import('./types').Endpoint>('/endpoints', { name, base_url, api_key }),
  endpointTest: (base_url: string, api_key: string) =>
    jpost<{ ok: boolean; models: string[]; provider: string; error?: string }>('/endpoints/test', { base_url, api_key }),
  endpointReprobe: (id: string) =>
    jpost<import('./types').Endpoint>(`/endpoints/${id}/probe`, {}),
  endpointUpdate: async (id: string, patch: { name: string; base_url: string; enabled: boolean; api_key?: string }) => {
    const r = await fetch(`/endpoints/${id}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch) });
    if (!r.ok) throw new Error(await detail(r));
    return r.json() as Promise<import('./types').Endpoint>;
  },
  endpointDelete: async (id: string) => {
    const r = await fetch(`/endpoints/${id}`, { method: 'DELETE' });
    if (!r.ok) throw new Error(await detail(r));
    return r.json() as Promise<{ ok: boolean }>;
  },
  // Mercury (reading engine) key — its own spot, separate from ingest endpoints.
  mercuryKey: () => jget<{ has_key: boolean }>('/reader/mercury'),
  setMercuryKey: async (api_key: string) => {
    const r = await fetch('/reader/mercury', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ api_key }) });
    if (!r.ok) throw new Error(await detail(r));
    return r.json() as Promise<{ has_key: boolean }>;
  },
  clearMercuryKey: async () => {
    const r = await fetch('/reader/mercury', { method: 'DELETE' });
    if (!r.ok) throw new Error(await detail(r));
    return r.json() as Promise<{ has_key: boolean }>;
  },
  learnUpload: async (vault: string, files: File[]) => {
    const fd = new FormData();
    fd.append('vault', vault);
    for (const f of files) fd.append('files', f);
    const r = await fetch('/learn/upload', { method: 'POST', body: fd });
    if (!r.ok) throw new Error(await detail(r));
    return r.json() as Promise<{ saved: number; skipped: number; sources: import('./types').LearnSources }>;
  },
  learnMeta: (vault: string, prompt: string, links: string[]) =>
    jpost<{ sources: import('./types').LearnSources }>('/learn/meta', { vault, prompt, links }),
  learnRemoveSource: async (vault: string, id: string) => {
    const r = await fetch(`/learn/source?vault=${encodeURIComponent(vault)}&id=${encodeURIComponent(id)}`, { method: 'DELETE' });
    if (!r.ok) throw new Error(await detail(r));
    return r.json() as Promise<{ sources: import('./types').LearnSources }>;
  },
  session: (body: { vault: string; engine?: string | null; voice?: string | null; level?: string | null; form?: string | null; language?: string | null; endpoint_id?: string | null; model?: string | null; dry?: boolean }) =>
    jpost<SessionInfo>('/session', body),

  branches: (sid: string) => jget<{ branches: Branch[] }>(`/branches?session=${sid}`),
  steer: (sid: string, text: string) => jpost<{ ok: boolean }>('/steer', { session: sid, text }),
  wander: (sid: string, value: number) => jpost<{ ok: boolean; wander: number }>('/wander', { session: sid, value }),
  voices: (sid: string) => jget<import('./types').VoiceInfo>(`/voices?session=${sid}`),
  setVoice: (sid: string, name: string) =>
    jpost<{ ok: boolean; voice: string; voice_id: string }>('/voice', { session: sid, name }),
  setLevel: (sid: string, name: string) =>
    jpost<{ ok: boolean; level: string }>('/level', { session: sid, name }),
  setForm: (sid: string, name: string) =>
    jpost<{ ok: boolean; form: string }>('/form', { session: sid, name }),
  setLanguage: (sid: string, name: string) =>
    jpost<{ ok: boolean; language: string }>('/language', { session: sid, name }),
  missed: (sid: string, n = 25) =>
    jget<{ embed_label: string; pairs: MissedPair[] }>(`/missed?session=${sid}&n=${n}`),
  timeline: (sid: string, minConf = 0.7) =>
    jget<import('./types').TimelineData>(`/timeline?session=${sid}&min_conf=${minConf}`),
  quiz: (sid: string, pages: string[], count = 5, types: string[] = []) =>
    jpost<{ questions: QuizQuestion[]; cost: number }>('/quiz', { session: sid, pages, count, types }),
  nodes: (sid: string, top = 14) =>
    jget<{ nodes: { id: string; title: string; centrality: number; seen: number }[] }>(
      `/nodes?session=${sid}&top=${top}`),
  state: (sid: string) => jget<{ cost: number; recap: string; branches: Branch[] }>(`/state?session=${sid}`),

  streamPage: (
    body: { session: string; action: string; plan_id?: string | null; start?: string; seed?: string | null; wander?: number; diffusing?: boolean },
    h: SSEHandlers<PageStart, PageDone>, signal?: AbortSignal,
  ) => streamPost('/page', body, h, signal),

  streamRepage: (
    body: { session: string; index?: number },
    h: SSEHandlers<PageStart, PageDone>, signal?: AbortSignal,
  ) => streamPost('/repage', body, h, signal),

  streamExpand: (
    body: { session: string; selected: string; before: string; after: string; mode: string },
    h: SSEHandlers<never, ExpandDone>, signal?: AbortSignal,
  ) => streamPost('/expand', body, h, signal),

  // ---- audio narration (Kokoro, server-side) ----
  ttsVoices: () => jget<{ available: boolean; error: string | null; voices: string[]; default: string }>('/tts/voices'),
  streamTts: (
    body: { text: string; voice?: string; speed?: number },
    h: { clip?: (p: { text: string; b64: string }) => void; done?: () => void; error?: (p: { message: string }) => void },
    signal?: AbortSignal,
  ) => streamPost('/tts', body, h as Record<string, (p: unknown) => void>, signal),
};
