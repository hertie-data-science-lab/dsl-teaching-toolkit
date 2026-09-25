// Words and dates, in the vocabulary of design/vocabulary.md. Engine identifiers (K4,
// will_be_skipped, release.now) never reach the screen except through these maps.

import type { Assignment, AssignmentState, Release, ReleaseState, StageState } from './types';

/** `v` as text, blank for null and undefined. */
export function str(v: unknown): string {
  return v === undefined || v === null ? '' : String(v);
}

/** Lower case, each run of other characters one hyphen, none at either end. */
export function kebab(s: string): string {
  return String(s ?? '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}

const DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export interface Zoned {
  y: number;
  m: number; // 1-12
  d: number;
  dow: number; // 0 = Sunday
  hh: string;
  mm: string;
  hasTime: boolean;
}

/** Calendar parts of `iso` in `tz`. A date-only value ("2026-10-07") is taken as that day. */
export function zoned(iso: string, tz = 'Europe/Berlin'): Zoned {
  const naive = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(:\d{2})?$/.exec(iso);
  if (naive) {
    // A schedule.yml time with no offset is already wall-clock time in the semester's timezone.
    const [y, m, d] = [Number(naive[1]), Number(naive[2]), Number(naive[3])];
    return { y, m, d, dow: new Date(Date.UTC(y, m - 1, d)).getUTCDay(), hh: naive[4], mm: naive[5], hasTime: true };
  }
  if (!iso.includes('T')) {
    const [y, m, d] = iso.split('-').map(Number);
    const dow = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
    return { y, m, d, dow, hh: '', mm: '', hasTime: false };
  }
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: tz,
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(new Date(iso));
  const get = (t: string) => parts.find((p) => p.type === t)?.value ?? '';
  const y = Number(get('year')), m = Number(get('month')), d = Number(get('day'));
  return { y, m, d, dow: new Date(Date.UTC(y, m - 1, d)).getUTCDay(), hh: get('hour'), mm: get('minute'), hasTime: true };
}

/** "Thu 24 Sep", with the year only when it is not `refYear`. */
export function fmtDay(iso: string | null | undefined, tz?: string, refYear?: number): string {
  if (!iso) return '';
  const z = zoned(iso, tz);
  return `${DOW[z.dow]} ${z.d} ${MON[z.m - 1]}${refYear !== undefined && z.y !== refYear ? ` ${z.y}` : ''}`;
}

export function fmtTime(iso: string | null | undefined, tz?: string): string {
  if (!iso) return '';
  const z = zoned(iso, tz);
  return z.hasTime ? `${z.hh}:${z.mm}` : '';
}

/** "Thu 24 Sep 10:00" */
export function fmtWhen(iso: string | null | undefined, tz?: string, refYear?: number): string {
  if (!iso) return '';
  const t = fmtTime(iso, tz);
  return `${fmtDay(iso, tz, refYear)}${t ? ` ${t}` : ''}`;
}

/** "24 Sep" */
export function fmtShort(iso: string, tz?: string): string {
  const z = zoned(iso, tz);
  return `${z.d} ${MON[z.m - 1]}`;
}

export function dayKey(iso: string, tz?: string): string {
  const z = zoned(iso, tz);
  return `${z.y}-${String(z.m).padStart(2, '0')}-${String(z.d).padStart(2, '0')}`;
}

/** A sortable wall-clock key in `tz`: "2026-09-24T10:00" (date-only values sort first in their day). */
export function sortKey(iso: string, tz?: string): string {
  const z = zoned(iso, tz);
  return `${dayKey(iso, tz)}T${z.hasTime ? `${z.hh}:${z.mm}` : '00:00'}`;
}

/** Whole days from `a` to `b` (both yyyy-mm-dd). */
export function daysBetween(a: string, b: string): number {
  const p = (s: string) => { const [y, m, d] = s.split('-').map(Number); return Date.UTC(y, m - 1, d); };
  return Math.round((p(b) - p(a)) / 864e5);
}

export function addDays(day: string, n: number): string {
  const [y, m, d] = day.split('-').map(Number);
  return new Date(Date.UTC(y, m - 1, d + n)).toISOString().slice(0, 10);
}

export function ago(iso: string | null | undefined, now: number): string {
  if (!iso) return 'never';
  const mins = Math.max(0, Math.round((now - Date.parse(iso)) / 60000));
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins} min ago`;
  const h = Math.round(mins / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.round(h / 24)} days ago`;
}

export const STAGE_WORD: Record<StageState, string> = { done: 'Done', todo: 'To do', blocked: 'Blocked', problem: 'Has a problem' };

export const COHORT_STAGES: [string, string][] = [
  ['K1', 'Org'], ['K2', 'Setup'], ['K3', 'Instructors'], ['K4', 'Schedule'], ['K5', 'Students'], ['K6', 'Site'], ['K7', 'Archive'],
];
export const COURSE_STAGES: [string, string][] = [
  ['C1', 'Org'], ['C2', 'Setup'], ['C3', 'Details'], ['C4', 'Materials'], ['C5', 'Assignment templates'], ['C6', 'Website'],
];

/** Where a problem sits, as the problem card's bold first word. */
export const PROBLEM_AREA: Record<string, string> = {
  K1: 'Org', K2: 'Setup', K3: 'Instructors', K4: 'Schedule', K5: 'Roster', K6: 'Site', K7: 'Archive',
  C1: 'Course org', C2: 'Course setup', C3: 'Course details', C4: 'Materials', C5: 'Template', C6: 'Public website',
};

export const ASSIGNMENT_WORD: Record<AssignmentState, string> = {
  declared: 'declared', teams_forming: 'teams forming', blocked: 'blocked', open: 'open',
  late_window: 'late window', marking: 'marking', returned: 'returned',
};

export const RELEASE_WORD: Record<ReleaseState, string> = {
  planned: 'planned', will_be_skipped: 'will be skipped', released: 'released', late: 'late',
};

/** Registry op names as the operations list names them. */
export const OP_LABEL: Record<string, string> = {
  'semester.check': 'Check',
  'semester.preview_automation': 'Preview the next automatic run',
  'release.now': 'Release',
  'release.early': 'Release early',
  'release.rerun': 'Release again',
  'release.adhoc': 'Release',
  'release.propagate_back': 'Keep for future semesters',
  'assignment.handout_now': 'Hand out',
  'assignment.update_copies': 'Update every copy',
  'assignment.collect_now': 'Collect submissions',
  'grades.return': 'Return marks',
  'roster.send_codes': 'Send new codes',
  'site.update': 'Update site',
  'access.check': 'Check instructor access',
  'semester.archive': 'Archive',
  'course.publish_website': 'Publish website',
  'assignment.derive_starter': 'Derive student version',
  'assignment.generate_syllabus': 'Generate syllabus',
  'materials.create': 'New materials',
  'assignment.create': 'New assignment',
  'semester.bootstrap': 'New semester',
  'teams.open_window': 'Email students without a team',
};

export function opLabel(op: string): string {
  return OP_LABEL[op] ?? op;
}

/** "Assignment 2" from the slug `assignment-2` (or `assignment-4-project`); else the slug. */
export function assignmentIdent(slug: string): string {
  const m = /^assignment-(\d+)/.exec(slug);
  return m ? `Assignment ${m[1]}` : slug;
}

export function assignmentTitle(a: Pick<Assignment, 'slug' | 'title'>): string {
  const id = assignmentIdent(a.slug);
  return a.title ? `${id}: ${a.title}` : id;
}

/** The identifier the site derives for a release: "Session 3", "Lab 2", "Readings". */
export function releaseIdent(r: Release, all: Release[]): string {
  if (r.kind === 'readings') return 'Readings';
  const word = r.kind === 'lab' ? 'Lab' : 'Session';
  const num = /(\d+)$/.exec(r.id); // `lecture-5`, `lab-3`: the label says its own ordinal
  if (num) return `${word} ${Number(num[1])}`;
  const same = all.filter((x) => x.kind === r.kind).sort((a, b) => a.when.localeCompare(b.when));
  const n = same.findIndex((x) => x.id === r.id) + 1;
  return `${word} ${n || same.length + 1}`;
}

export const TYPE_CLASS: Record<string, string> = {
  lecture: 'lec', lab: 'lab', readings: 'lec', handout: 'asg', due: 'asg', exam: 'exam',
  special_event: 'evt', event: 'evt', term: 'term', archive: 'term', release: 'term',
};
export const TYPE_LABEL: Record<string, string> = {
  lecture: 'lecture', lab: 'lab', readings: 'readings', handout: 'hand out', due: 'due', exam: 'exam',
  special_event: 'event', event: 'event', term: 'semester', archive: 'archive', release: 'release',
};

// ------------------------------------------------------------------ markdown (as the site renders `details`)

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function inline(s: string): string {
  return s
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<b>$1</b>')
    .replace(/\*([^*]+)\*/g, '<i>$1</i>')
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}

/** Escaped HTML for a small markdown subset: paragraphs, hard breaks, `- ` lists, code, bold, italic, links. */
export function md(src: string | null | undefined): string {
  if (!src || !String(src).trim()) return '';
  return esc(String(src).trim())
    .split(/\n{2,}/)
    .map((b) => {
      const lines = b.split('\n');
      // A paragraph may run straight into a list ("Intro:\n- a\n- b"): split at the first item.
      const first = lines.findIndex((l) => ITEM.test(l));
      if (first > 0 && lines.slice(first).every((l) => ITEM.test(l))) return para(lines.slice(0, first)) + list(lines.slice(first));
      return first === 0 && lines.every((l) => ITEM.test(l)) ? list(lines) : para(lines);
    })
    .join('');
}

const ITEM = /^\s*- /;

function list(lines: string[]): string {
  return `<ul>${lines.map((l) => `<li>${inline(l.replace(ITEM, ''))}</li>`).join('')}</ul>`;
}

/** As markdown does: a single newline is a space; a line ending in two spaces breaks. */
function para(lines: string[]): string {
  return `<p>${inline(lines.map((l, i) => (i === lines.length - 1 ? l : / {2,}$/.test(l) ? `${l.trimEnd()}<br>` : `${l.trimEnd()} `)).join(''))}</p>`;
}
