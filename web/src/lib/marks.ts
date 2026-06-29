// Parse the renderer's light markdown into CLEAN text + emphasis "marks" (char ranges).
//
// The narrated/canonical text MUST stay markup-free: TTS reads page.text and the
// karaoke/clarify offset-map (Reader `proseSegs`) aligns the rendered DOM to it by
// character content. So markup is never stored in page.text — it's parsed away into
// `marks`, which the reader renders as real <strong>/<em>/heading elements (adding
// elements, not characters → text content unchanged → highlight Ranges still align).
//
// Supported subset (everything else is left literal):
//   **bold**            → strong            *italic* / _italic_ → em
//   ^# heading           → h1 (line)         ^## heading         → h2 (line)
// Tolerant of unclosed markers (left literal) so partial streaming frames don't garble.
import type { Mark } from './types';

function parseInline(line: string, base: number, out: { marks: Mark[] }): string {
  let clean = '';
  let i = 0;
  while (i < line.length) {
    if (line.startsWith('**', i)) {                          // strong
      const close = line.indexOf('**', i + 2);
      if (close > i + 1) {
        const s = base + clean.length;
        clean += line.slice(i + 2, close);
        out.marks.push({ start: s, end: base + clean.length, kind: 'strong' });
        i = close + 2; continue;
      }
    }
    const ch = line[i];
    if (ch === '*' || ch === '_') {                          // em
      const close = line.indexOf(ch, i + 1);
      if (close > i + 1) {                                   // need ≥1 char inside
        const s = base + clean.length;
        clean += line.slice(i + 1, close);
        out.marks.push({ start: s, end: base + clean.length, kind: 'em' });
        i = close + 1; continue;
      }
    }
    clean += ch; i++;
  }
  return clean;
}

export function parseMarks(raw: string): { text: string; marks: Mark[] } {
  if (!raw) return { text: '', marks: [] };
  const out = { marks: [] as Mark[] };
  const lines = raw.split('\n');
  let clean = '';
  for (let li = 0; li < lines.length; li++) {
    if (li > 0) clean += '\n';
    let line = lines[li];
    let hk: 'h1' | 'h2' | null = null;
    const hm = /^(#{1,2})\s+(?=\S)/.exec(line);              // heading needs content after the marker
    if (hm) { hk = hm[1].length === 1 ? 'h1' : 'h2'; line = line.slice(hm[0].length); }
    const lineStart = clean.length;
    clean += parseInline(line, lineStart, out);
    if (hk && clean.length > lineStart) out.marks.push({ start: lineStart, end: clean.length, kind: hk });
  }
  return { text: clean, marks: out.marks };
}
