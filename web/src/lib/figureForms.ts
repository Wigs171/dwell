// Figure × form affinity — which text-figures make sense in which output forms.
//
// A figure isn't eligible just because the PAGE affords it; the active FORM has
// to welcome it too (a stepped-list is native to a guided tour, nonsense in a
// dialogue). Three levels per (figure, form):
//   • native  — the figure's HOME form; the scheduler should prefer it here.
//   • allowed — fine to appear.
//   • blocked — must NEVER appear (anything not listed below is blocked).
//
// This is the source of truth the eventual engine scheduler mirrors
// (`_build_figure_schedule` reads the session's `form`); the lab renders it as a
// per-cell badge + dims blocked figures so the matrix can be eyeballed/tuned.
import type { TextFigureId } from './types';

export type FormId = 'article' | 'guided' | 'qa' | 'dialogue';
export const FORMS: { id: FormId; label: string }[] = [
  { id: 'article', label: 'Article' },
  { id: 'guided', label: 'Guided tour' },
  { id: 'qa', label: 'Q&A' },
  { id: 'dialogue', label: 'Dialogue' },
];

export type Affinity = 'native' | 'allowed' | 'blocked';

// native/allowed per figure; every form NOT listed is blocked.
const AFF: Record<TextFigureId, { native: FormId[]; allowed: FormId[] }> = {
  // Framing/entry devices — broadly form-neutral, at home in an article.
  kicker:           { native: ['article'], allowed: ['guided', 'qa', 'dialogue'] },
  'headline-stack': { native: ['article'], allowed: ['guided', 'qa', 'dialogue'] },
  deck:             { native: ['article'], allowed: ['guided', 'qa', 'dialogue'] },
  // Summary/teaching devices — a lesson loves them; a dialogue must NOT
  // pre-summarize itself (the journey is the point).
  tldr:             { native: ['article'], allowed: ['guided', 'qa'] },
  'key-takeaways':  { native: ['guided'], allowed: ['article', 'qa'] },
  callout:          { native: ['guided'], allowed: ['article', 'qa'] },
  // Quotation — pull-quote lifts a striking line (incl. a line of dialogue);
  // a FAQ pulling a quote reads oddly.
  'pull-quote':     { native: ['article'], allowed: ['guided', 'dialogue'] },
  'block-quote':    { native: ['article'], allowed: ['guided', 'qa', 'dialogue'] },
  // Sequence — native to the guided tour; blocked in Q&A/dialogue.
  'stepped-list':   { native: ['guided'], allowed: ['article'] },
  // Contrast — a dialogue ALREADY contrasts two positions, so a box is redundant.
  comparison:       { native: ['article'], allowed: ['guided', 'qa'] },
  // Progressive disclosure — native to Q&A (expandable answers).
  accordion:        { native: ['qa'], allowed: ['article', 'guided'] },
  // Chrome / navigation / trust — form-neutral.
  'read-time':      { native: [], allowed: ['article', 'guided', 'qa', 'dialogue'] },
  sidenote:         { native: ['article'], allowed: ['guided', 'qa', 'dialogue'] },
  'see-also':       { native: [], allowed: ['article', 'guided', 'qa', 'dialogue'] },
  'source-strip':   { native: [], allowed: ['article', 'guided', 'qa', 'dialogue'] },
  // Opening flourish — editorial; a drop-cap on a FAQ/dialogue is wrong.
  'drop-cap':       { native: ['article'], allowed: [] },
  'raised-initial': { native: ['article'], allowed: ['guided'] },
  // Data devices — fit exposition; mostly out of place in a dialogue.
  'big-number':     { native: ['article'], allowed: ['guided', 'qa'] },
  glossary:         { native: ['guided'], allowed: ['article', 'qa'] },
  definition:       { native: ['guided'], allowed: ['article', 'qa', 'dialogue'] },
  timeline:         { native: ['article'], allowed: ['guided'] },
};

export function affinity(id: TextFigureId, form: FormId): Affinity {
  const a = AFF[id];
  if (!a) return 'allowed';
  if (a.native.includes(form)) return 'native';
  if (a.allowed.includes(form)) return 'allowed';
  return 'blocked';
}

export function figureFitsForm(id: TextFigureId, form: FormId): boolean {
  return affinity(id, form) !== 'blocked';
}
