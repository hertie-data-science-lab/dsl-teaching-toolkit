// The run settings as form fields, at one layer of the cascade (model/cascade.ts): the
// course's `assignment_defaults`, a semester's `defaults:`, or one assignment's block. Each
// field shows, in grey, what applies when it is left empty: the value the next layer down
// gives and where that comes from.

import { RUN_KEYS, SOURCE_WORD, TEAM_FORMATION, VISIBILITY, valueWord, type Effective, type RunKey } from '../model/cascade';
import { penaltyError } from '../model/policy';
import { opt, type FieldTier, type Tiers, type Values } from './types';

export const RUN_LABEL: Record<RunKey, string> = {
  team_formation: 'How teams form',
  max_team_size: 'Max team size',
  late_window_days: 'Late window, days',
  late_penalty_per_day: 'Late penalty per day',
  visibility: 'Who can see each student’s repo',
  submit_url: 'Link to where they submit',
};

const REASON: Record<RunKey, string> = {
  team_formation: 'For team assignments. You assign them on the assignment’s Teams page.',
  max_team_size: 'For team assignments: students cannot join a team that is full.',
  late_window_days: 'Late work is accepted for this many days after the due date; 0 means none. The late cutoff is the due date plus these days.',
  late_penalty_per_day: 'Taken off the earned mark for each day started after the due date.',
  visibility: 'Applies to copies handed out after this change; existing copies keep theirs.',
  submit_url: 'For assignments submitted elsewhere: shown on the student site beside the due date.',
};

const blank = (x: unknown) => x === undefined || x === null || x === '';

/** Why the late pair as typed would not do what it says, or null. A layer naming one half sets the other to none. */
export function lateError(v: Values): string | null {
  const days = v.late_window_days, pen = v.late_penalty_per_day;
  if (!blank(pen) && blank(days)) return 'Set the late window too: a penalty alone sets the window to none.';
  if (!blank(days) && days !== 0 && blank(pen)) return 'Set the penalty too: a window alone sets the penalty to none. Write 0% for no penalty.';
  return null;
}

/** One run setting as a field; `fallback` is what applies when it is left empty. */
export function runTier(key: RunKey, fallback: Effective, tier: FieldTier['tier'] = 'default'): FieldTier {
  const grey = `${SOURCE_WORD[fallback.source]}: ${valueWord(key, fallback.value)}`;
  const base = { tier, label: RUN_LABEL[key], reason: REASON[key], defaultLabel: grey };
  const choose = (labels: Record<string, string>) => [opt('', `Default (${valueWord(key, fallback.value)})`), ...Object.entries(labels).map(([v, l]) => opt(v, l))];
  switch (key) {
    case 'team_formation':
      return { ...base, widget: 'select', options: choose(TEAM_FORMATION) };
    case 'visibility':
      return { ...base, widget: 'select', options: choose(VISIBILITY) };
    case 'max_team_size':
      return { ...base, widget: 'number', placeholder: String(fallback.value ?? ''), check: (x) => (blank(x) || (Number.isInteger(x) && (x as number) >= 1) ? null : 'A whole number, 1 or more.') };
    case 'late_window_days':
      return { ...base, widget: 'number', placeholder: String(fallback.value ?? ''), check: (x, v) => (!blank(x) && !(Number.isInteger(x) && (x as number) >= 0) ? 'A whole number of days, 0 or more.' : lateError(v)) };
    case 'late_penalty_per_day':
      return { ...base, placeholder: String(fallback.value ?? ''), check: (x) => penaltyError(x) };
    case 'submit_url':
      return { ...base, widget: 'url', placeholder: 'https://', check: (x) => (blank(x) || /^https:\/\/\S+$/.test(String(x)) ? null : 'The link must start with https://.') };
  }
}

/** The run settings at one layer, in the order an instructor thinks; `keys` limits which. */
export function runTiers(fallback: (k: RunKey) => Effective, keys: readonly RunKey[] = RUN_KEYS): Tiers {
  return Object.fromEntries(keys.map((k) => [k, runTier(k, fallback(k))]));
}
