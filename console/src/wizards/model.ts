// What the wizards derive and decide, as pure functions: org and repo names (research/07
// section 1), terms, which step a wizard opens at (inputs.md rule 8), the two forbidden
// format pairs (research/06 C1, C2) and the request args each wizard sends.

import { termOf } from '../model/discovery';
import { kebab } from '../model/format';
import { DEFAULT_FORMATS, ORG_NAME_RE } from '../model/policy';
import type { Values } from '../tiers/types';

/** The lab's bot: an owner of every course and semester org until the console app replaces it. */
export const BOT = 'hertie-dsl-bot';

/** `hertie-<course-slug>-<code>`: lower case, no year; the code is folded to lower case. */
export function courseOrgName(name: string, code: string): string {
  const parts = ['hertie', kebab(name), kebab(code)].filter(Boolean);
  return parts.join('-');
}

/** The course slug inside a course org's name: `hertie-dsl-demo-course-e1234` -> `dsl-demo-course`. */
export function courseSlugOf(org: string, code: string): string {
  let s = org.toLowerCase().replace(/^hertie-/, '');
  const c = kebab(code);
  if (c && s.endsWith(`-${c}`)) s = s.slice(0, -(c.length + 1));
  return s;
}

/** `hertie-<course-slug>-<termtag>`. */
export function cohortOrgName(courseOrg: string, code: string, term: string): string {
  return `hertie-${courseSlugOf(courseOrg, code)}-${term}`;
}

export const ORG_RE = ORG_NAME_RE;
export const TERM_RE = /^[fs]\d{4}$/;

export function termLabel(term: string): string {
  return termOf(`x-${term}`).label;
}

/** The semester after `term`: f2026 -> s2027 -> f2027. */
export function nextTerm(term: string): string {
  const m = /^([fs])(\d{4})$/.exec(term);
  if (!m) return term;
  return m[1] === 'f' ? `s${Number(m[2]) + 1}` : `f${m[2]}`;
}

/** The semester a course starts next, from the date: Fall from January to July, else next Spring. */
export function upcomingTerm(now: number): string {
  const d = new Date(now);
  return d.getUTCMonth() <= 6 ? `f${d.getUTCFullYear()}` : `s${d.getUTCFullYear() + 1}`;
}

/** Terms a new semester can be for: the two after the newest one (or after today), none taken. */
export function cohortTerms(existing: string[], now: number): string[] {
  const first = existing.length ? nextTerm(existing[0]) : upcomingTerm(now);
  return [first, nextTerm(first)].filter((t) => !existing.includes(t));
}

/** Terms a template or materials repo can be for: every semester's, newest first, then the next. */
export function contentTerms(existing: string[], now: number): string[] {
  const next = existing.length ? nextTerm(existing[0]) : upcomingTerm(now);
  return [...existing, next].filter((t, i, a) => a.indexOf(t) === i);
}

/** `assignment-<n>-<term>`, the name scaffold gives a template. */
export function templateRepo(number: string | number | undefined, term: string): string {
  return `assignment-${number === undefined || number === '' ? 'N' : number}-${term}`;
}

/** The next number no template of `term` uses. */
export function nextFreeNumber(repos: string[], term: string): number {
  const used = repos.map((r) => new RegExp(`^assignment-(\\d+)-${term}$`).exec(r)).filter(Boolean).map((m) => Number(m![1]));
  return used.length ? Math.max(...used) + 1 : 1;
}

export function materialsRepo(term: string): string {
  return `course-materials-${term}`;
}

// ------------------------------------------------------------------ steps

/**
 * The step a wizard shows when asked for `requested` (1-based): any step up to the first one
 * not yet done, never past it. A step whose predicate is already true is skipped on the way.
 */
export function openAt(done: boolean[], requested?: number): number {
  const first = done.findIndex((d) => !d);
  const limit = first < 0 ? done.length : first + 1;
  if (!requested || requested < 1) return limit;
  return Math.min(requested, limit);
}

/** The fields of one step as a stable string: a step stays verified only while they do. */
export function signature(v: Values, keys: string[]): string {
  return JSON.stringify(keys.map((k) => v[k] ?? null));
}

// ------------------------------------------------------------------ New assignment

/**
 * Why `f` cannot be added to `formats` now, or null when it can: "none" stands alone, R
 * Markdown and Quarto never go together (C1), a notebook and Python files never go together
 * while tests run (C2).
 */
export function formatBlock(formats: string[], f: string, autograde: boolean): string | null {
  if (formats.includes(f)) return null;
  if (f === 'none') return null; // ticking it clears the rest
  if (formats.includes('none')) return '“No starter file” stands alone.';
  if ((f === 'qmd' && formats.includes('rmd')) || (f === 'rmd' && formats.includes('qmd'))) return 'R Markdown and Quarto cannot be combined.';
  if (autograde && ((f === 'py' && formats.includes('ipynb')) || (f === 'ipynb' && formats.includes('py')))) return 'A notebook and Python files together cannot run automatic tests.';
  return null;
}

/** Why automatic tests cannot be switched on, or null when they can. */
export function autogradeBlock(v: Values): string | null {
  const formats = (v.formats as string[] | undefined) ?? [];
  if (v.submit_via === 'shared_dropbox_repo') return 'A shared drop box holds every student’s work in one repo, so tests cannot run per student.';
  if (formats.includes('ipynb') && formats.includes('py')) return 'A notebook and Python files together cannot run automatic tests. Pick one format to turn tests on.';
  if (formats.length && formats.every((f) => f === 'latex' || f === 'none'))
    return 'A written report has no code for tests to run. Pick a notebook or code format to turn tests on.';
  return null;
}

/** The two refused combinations, whichever way they were reached (a resumed draft, say). */
export function formatError(v: Values): string | null {
  const f = (v.formats as string[] | undefined) ?? [];
  if (!f.length) return 'Choose at least one.';
  if (f.includes('none') && f.length > 1) return '“No starter file” stands alone.';
  if (f.includes('rmd') && f.includes('qmd')) return 'R Markdown and Quarto cannot be combined.';
  if (v.autograde === 'true' && f.includes('ipynb') && f.includes('py')) return 'A notebook and Python files together cannot run automatic tests.';
  return null;
}

/** Toggle `f` in the picker; ticking "No starter file" clears the rest. */
export function toggleFormat(formats: string[], f: string): string[] {
  if (formats.includes(f)) return formats.filter((x) => x !== f);
  if (f === 'none') return ['none'];
  return [...formats.filter((x) => x !== 'none'), f];
}

/** The `assignment.create` args (schemas/ops.json) for the wizard's values. */
export function assignmentArgs(v: Values): Record<string, unknown> {
  const base = { name: v.name, number: v.number === undefined ? undefined : String(v.number), semester: v.term };
  if (v.copy_from) return { ...base, copy_from: v.copy_from };
  const group = v.type === 'group';
  const submit = v.submit_via ?? 'assignment_repo';
  return {
    ...base,
    type: group ? 'group' : 'individual',
    submit_via: submit,
    formats: ((v.formats as string[] | undefined) ?? DEFAULT_FORMATS).join(','),
    autograde: !autogradeBlock(v) && v.autograde === 'true',
  };
}

/** The `materials.create` args for the New materials form. */
export function materialsArgs(v: Values): Record<string, unknown> {
  if (v.copy_from) return { semester: v.term, copy_from: v.copy_from };
  const open = v.open === true;
  return { semester: v.term, public_dirs: open ? v.public_dirs ?? 'lectures' : '(nothing public)', public_types: open ? v.public_types ?? 'html + pdf' : undefined };
}
