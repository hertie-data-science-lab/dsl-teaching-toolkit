// Course details (`.github/course.yml`, decision 0039): design/inputs.md "Set up a course". The
// admins list is edited as rows beside this form. A level's `defaults:` (course or semester) is
// one form: each field shows, in grey, the value it inherits and where that comes from (the
// engine's answer, status.json), the one that applies when it is left empty. The default
// destination (`materials_repo`) is a select over the level's Release into list when the page
// draws that list (plan 2026-11-04, decision 7).

import { REPO_NAME_RE, SOURCE_NAME, SOURCE_WORD, effectiveOf, valueWord, type Effective, type Effectives, type RunKey, type SemesterWideKey } from '../model/settings';
import { CONTACTS, DEFAULT_TIMEZONE, POLICY } from '../model/policy';
import { blank } from '../edit/yamlText';
import { runTiers } from './runSettings';
import { defaultFirst, type Tiers, type Values } from './types';

const opt = (value: string, label: string) => ({ value, label });

/** Timezones the schedule offers: the institution's first. */
export const TIMEZONES = [...new Set([DEFAULT_TIMEZONE, 'Europe/London', 'Europe/Paris', 'UTC', 'America/New_York'])];

export const ABOUT: Tiers = {
  course_name: { tier: 'ask', label: 'Course name', reason: 'Appears on every semester’s student site.' },
  course_code: { tier: 'ask', label: 'Course code', reason: 'Upper case here; lower case in the org name.' },
  course_description: { tier: 'default', label: 'Description', widget: 'markdown', reason: 'One paragraph. Students see it on every semester’s student site home page, and the public website shows it too.' },
};

const LICENCE = POLICY.licences[0].name;

/** Course facts the public website shows (decision 0019); each falls back to the institution's, shown grey. */
export const COURSE_FACTS: Tiers = {
  contact: {
    tier: 'default', label: 'Contact email', widget: 'email', placeholder: CONTACTS[0], defaultLabel: `institution default: ${CONTACTS.join(', ')}`,
    check: (v) => (v && !/^[^@\s]+@[^@\s]+$/.test(String(v)) ? 'Write an email address.' : null),
  },
  licence: {
    tier: 'default', label: 'Licence', widget: 'select', defaultLabel: `institution default: ${LICENCE}`,
    options: [opt('', `${LICENCE} (default)`), opt(LICENCE, `${LICENCE}, fixed for this course`), ...POLICY.licences.slice(1).map((l) => opt(l.name, l.name))],
  },
};


/** The run settings a level's `defaults:` states (`settings.DEFAULT_KEYS`): no submit link, which is the assignment's. */
export const DEFAULT_RUN_KEYS: RunKey[] = ['max_team_size', 'late_window_days', 'late_penalty_per_day', 'team_formation', 'visibility', 'marks_per_question'];

/**
 * The default destination's choices: the inherited repo first, as "<repo>, from the course
 * (default)" with value `''`, then every other repo of `into`, and the saved value when the list
 * no longer has it (a pin of the inherited repo shows as "set here", as `defaultFirst` does).
 */
export function destinationOptions(fallback: Effective, into: string[], saved: unknown): { value: string; label: string }[] {
  const fb = String(fallback.value ?? '');
  const cur = blank(saved) ? '' : String(saved);
  const rest = [...new Set([...into, ...(cur ? [cur] : [])])].filter((n) => n && n !== fb);
  return [opt('', `${fb}, from ${SOURCE_NAME[fallback.source]} (default)`), ...(cur === fb && cur ? [opt(fb, `${fb} (set here)`)] : []), ...rest.map((n) => opt(n, n))];
}

/**
 * The `defaults:` keys that hold for a whole semester, as fields; `fallback` is what applies when
 * one is left empty. `into`, the level's Release into list, makes the default destination a select
 * over it; without it (New course) the field is a repo name.
 */
function semesterWideTiers(fallback: (k: string) => Effective, saved: Values, into?: string[]): Tiers {
  const grey = (k: SemesterWideKey) => `${SOURCE_WORD[fallback(k).source]}: ${valueWord(k, fallback(k).value)}`;
  const tz = fallback('timezone');
  const sync = fallback('autosync_released_edits');
  return {
    timezone: {
      tier: 'default', label: 'Timezone', widget: 'select', reason: 'The semester’s dates and times are read in it.', defaultLabel: grey('timezone'),
      options: defaultFirst(tz.value, String(tz.value ?? DEFAULT_TIMEZONE), TIMEZONES.map((t) => opt(t, t)), saved.timezone),
    },
    archive_grace_days: {
      tier: 'default', label: 'Days after the semester ends that it is archived', widget: 'number', defaultLabel: grey('archive_grace_days'), placeholder: String(fallback('archive_grace_days').value ?? ''),
      reason: 'Unless the schedule gives an archive date.', check: (x) => (blank(x) || (Number.isInteger(x) && (x as number) >= 0) ? null : 'A whole number of days, 0 or more.'),
    },
    materials_repo: {
      tier: 'default', label: 'Default destination', defaultLabel: grey('materials_repo'), placeholder: String(fallback('materials_repo').value ?? ''),
      hint: { text: 'Where a release lands when its entry names no repo. Set by the institution, overridable here and per semester.' },
      check: (x) => (blank(x) || REPO_NAME_RE.test(String(x).trim()) ? null : 'Not a repo name (letters, digits, `.`, `_`, `-`).'),
      ...(into ? { widget: 'select' as const, options: destinationOptions(fallback('materials_repo'), into, saved.materials_repo) } : {}),
    },
    autosync_released_edits: {
      tier: 'default', label: 'Send fixes to released files automatically', widget: 'select', defaultLabel: grey('autosync_released_edits'),
      reason: 'On: an edit to a course folder that has already been released reaches students on the next scheduled run. Off: a release is one copy; use Release again after a fix.',
      options: defaultFirst(sync.value, valueWord('autosync_released_edits', sync.value), [opt('true', 'On'), opt('false', 'Off')], saved.autosync_released_edits),
    },
  };
}

/**
 * A level's `defaults:` as fields: the run settings, then the semester-wide ones. `inherited`
 * is what each key falls back to at this level; `saved` is the level as its file holds it, so a
 * value pinned equal to the inherited one shows as "set here". `into` as `semesterWideTiers` takes it.
 */
export function defaultsTiers(inherited: Effectives, saved: Values = {}, into?: string[]): Tiers {
  const fallback = (k: string) => effectiveOf(inherited, k);
  return { ...runTiers(fallback, DEFAULT_RUN_KEYS, saved), ...semesterWideTiers(fallback, saved, into) };
}

/** The keys a defaults form edits, as the form spells them (`visibility`). */
