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

export type FormId =
  | 'article' | 'guided' | 'qa' | 'dialogue' | 'story'
  | 'tutorial' | 'brief' | 'case' | 'interview' | 'debate' | 'epistolary' | 'chronicle';
export const FORMS: { id: FormId; label: string }[] = [
  { id: 'article', label: 'Article' },
  { id: 'guided', label: 'Guided tour' },
  { id: 'qa', label: 'Q&A' },
  { id: 'dialogue', label: 'Dialogue' },
  { id: 'story', label: 'Story' },
  { id: 'tutorial', label: 'Tutorial' },
  { id: 'brief', label: 'Brief' },
  { id: 'case', label: 'Case study' },
  { id: 'interview', label: 'Interview' },
  { id: 'debate', label: 'Debate' },
  { id: 'epistolary', label: 'Letters' },
  { id: 'chronicle', label: 'Chronicle' },
];

export type Affinity = 'native' | 'allowed' | 'blocked';

// native/allowed per figure; every form NOT listed is blocked.
const AFF: Record<TextFigureId, { native: FormId[]; allowed: FormId[] }> = {
  // Framing/entry devices — broadly form-neutral, at home in an article. The spoken
  // forms (dialogue/interview/debate) and letters keep chrome light.
  kicker:           { native: ['article'], allowed: ['guided', 'qa', 'dialogue', 'brief', 'case', 'chronicle'] },
  'headline-stack': { native: ['article'], allowed: ['guided', 'qa', 'dialogue', 'brief', 'case'] },
  deck:             { native: ['article'], allowed: ['guided', 'qa', 'dialogue', 'brief', 'case', 'chronicle'] },
  // Summary/teaching devices — a lesson loves them; a brief IS a bottom line; a
  // dialogue/story must NOT pre-summarize itself (the journey is the point).
  tldr:             { native: ['brief'], allowed: ['article', 'guided', 'qa'] },
  'key-takeaways':  { native: ['guided'], allowed: ['article', 'qa', 'tutorial', 'brief', 'case'] },
  callout:          { native: ['guided'], allowed: ['article', 'qa', 'tutorial', 'brief'] },
  // Quotation — pull-quote lifts a striking line (incl. a spoken turn or a letter's
  // line); a FAQ pulling a quote reads oddly.
  'pull-quote':     { native: ['article'], allowed: ['guided', 'dialogue', 'story', 'interview', 'debate', 'epistolary', 'case'] },
  'block-quote':    { native: ['article'], allowed: ['guided', 'qa', 'dialogue', 'interview', 'case', 'chronicle'] },
  // Sequence — native to the hands-on lesson; a guided tour may use it; blocked in
  // Q&A/dialogue/story (a scene never numbers its steps).
  'stepped-list':   { native: ['tutorial'], allowed: ['guided', 'article'] },
  // Contrast — a dialogue/debate ALREADY contrasts two positions, so a box is redundant.
  comparison:       { native: ['article'], allowed: ['guided', 'qa', 'brief', 'case'] },
  // Progressive disclosure — native to Q&A (expandable answers).
  accordion:        { native: ['qa'], allowed: ['article', 'guided', 'tutorial'] },
  // Chrome / navigation / trust — form-neutral.
  'read-time':      { native: [], allowed: ['article', 'guided', 'qa', 'dialogue', 'story', 'tutorial', 'brief', 'case', 'interview', 'debate', 'epistolary', 'chronicle'] },
  sidenote:         { native: ['article'], allowed: ['guided', 'qa', 'dialogue', 'tutorial', 'brief', 'case', 'chronicle'] },
  'see-also':       { native: [], allowed: ['article', 'guided', 'qa', 'dialogue', 'story', 'tutorial', 'brief', 'case', 'interview', 'debate', 'epistolary', 'chronicle'] },
  'source-strip':   { native: [], allowed: ['article', 'guided', 'qa', 'dialogue', 'brief', 'case', 'chronicle'] },
  // Opening flourish — editorial; a drop-cap opens a story, letter, or article well,
  // but is wrong on a FAQ/dialogue/brief.
  'drop-cap':       { native: ['article'], allowed: ['story', 'epistolary'] },
  'raised-initial': { native: ['article'], allowed: ['guided', 'story', 'epistolary', 'chronicle'] },
  // Data devices — fit exposition; mostly out of place in spoken forms and scenes.
  'big-number':     { native: ['article'], allowed: ['guided', 'qa', 'brief', 'case', 'chronicle'] },
  glossary:         { native: ['guided'], allowed: ['article', 'qa', 'tutorial'] },
  definition:       { native: ['guided'], allowed: ['article', 'qa', 'dialogue', 'tutorial'] },
  timeline:         { native: ['chronicle'], allowed: ['article', 'guided', 'case'] },
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
