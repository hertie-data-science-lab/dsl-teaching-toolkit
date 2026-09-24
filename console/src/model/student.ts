// What a student's screens know about a semester that is the same for every student: the
// schedule rows with their kinds, the assignments' dates and rules, the instructors' cards,
// the archive date. It comes through ONE interface, `StudentData`, because its source will
// change: today it is the public site repo's generated files (`SiteSource`); once the engine
// writes `.github/.system/student-status.json` (WP-D4), a source reading that one file
// replaces it and no screen changes. The semester's own `status.json` is private
// (`classroom-config`), so a student can never read it.
//
// A student's own facts (their repos, team, receipts, gradebook) do not come from here:
// they come from GitHub directly, with the student's token (`model/mine.ts`).

import { parse } from 'yaml';
import type { DirEntry, GitHubClient } from '../github/client';
import { addDays, str } from './format';

/** One row of the semester calendar. `when` is wall-clock time in the semester's timezone ("2026-09-22T10:00:00") or a full ISO instant. */
export interface ScheduleRow {
  id: string;
  /** lecture | lab | assignment (hand out) | due | exam | special_event | term_date, or a kind added later. */
  kind: string;
  when: string;
  /** The row is about a day, not a time (term dates, the archive). */
  allDay: boolean;
  title: string;
  subtitle: string;
  details: string;
  /** The assignment slug, for hand-out and due rows. */
  assignment?: string;
  /** False for a session whose materials are not out yet. */
  released: boolean;
  /** Released files: `path` within `repo` when they live in a materials repo the console can open. */
  links: { name: string; repo?: string; path?: string; url: string }[];
}

export type SubmitVia = 'assignment_repo' | 'shared_dropbox_repo' | 'external' | '';

export interface SemesterAssignment {
  slug: string;
  /** "Assignment 3 Project" */
  title: string;
  /** "Group project" */
  subtitle: string;
  handout: string | null;
  due: string | null;
  /** The late cutoff: due + the late window; null when the source does not say. */
  lateCutoff: string | null;
  /** "10% per day, up to 10 days" */
  lateRule: string;
  /** "What is on main at the grading cutoff is what is marked." */
  cutoffSentence: string;
  submitVia: SubmitVia;
  /** Only an assignment_repo is private per unit, and only it carries a Submission receipts issue. */
  privateRepo: boolean;
  /** Where an external assignment is handed in. */
  submitUrl: string;
  group: boolean;
  /** While students may form or join teams; null when there is no open window. */
  teamFormation: { closes: string; cap: number | null } | null;
  /** When the solution becomes visible; null when none is scheduled (or the source does not say). */
  solutionShown: string | null;
  maxPoints: string;
  handedOut: boolean;
}

export interface InstructorCard {
  name: string;
  title: string;
  webpage: string;
  /** An image URL the console may load, or '' (initials are shown instead). */
  picture: string;
  role: 'instructor' | 'teaching_assistant';
}

export interface SemesterFacts {
  timezone: string;
  rows: ScheduleRow[];
  assignments: SemesterAssignment[];
  instructors: InstructorCard[];
  /** When every repo of the semester becomes read-only; null when not scheduled. */
  archive: string | null;
  /** The course's late-work sentences. */
  latePolicy: string[];
  /** The materials repos released files live in (normally one, `materials`). */
  materialsRepos: string[];
}

/** Where a student's screens get the semester's shared facts. */
export interface StudentData {
  /** The semester's facts, or null when the source has nothing for it. */
  facts(org: string): Promise<SemesterFacts | null>;
}

export const DEFAULT_TZ = 'Europe/Berlin';
/** How long a semester's facts are reused before they are read again (ETag'd: an unchanged file costs no rate limit). */
export const FRESH_MS = 10 * 60 * 1000;

// --------------------------------------------------------------------------- time

/** `iso` as an epoch millisecond. A time with no offset is wall-clock time in `tz`; a date alone is its midnight. */
export function instant(iso: string, tz = DEFAULT_TZ): number {
  if (/[zZ]$|[+-]\d{2}:?\d{2}$/.test(iso)) return Date.parse(iso);
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?$/.exec(iso.trim());
  if (!m) return Date.parse(iso);
  const wall = Date.UTC(+m[1], +m[2] - 1, +m[3], +(m[4] ?? 0), +(m[5] ?? 0), +(m[6] ?? 0));
  // The zone's offset at that moment, found twice so a time next to a clock change lands right.
  let t = wall - offsetAt(wall, tz);
  t = wall - offsetAt(t, tz);
  return t;
}

function offsetAt(t: number, tz: string): number {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: tz, year: 'numeric', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
  }).formatToParts(new Date(t));
  const g = (k: string) => Number(parts.find((p) => p.type === k)?.value ?? 0);
  return Date.UTC(g('year'), g('month') - 1, g('day'), g('hour'), g('minute'), g('second')) - Math.floor(t / 1000) * 1000;
}

/** Midnight at the start of the day `now` falls on, in `tz`. */
export function startOfDay(now: number, tz = DEFAULT_TZ): number {
  const d = new Intl.DateTimeFormat('en-CA', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(now));
  return instant(d, tz);
}

/** The late cutoff from the late rule's window ("up to 10 days"; "not accepted after the deadline" is the deadline). */
export function cutoffFrom(due: string | null, lateRule: string): string | null {
  if (!due) return null;
  if (/not accepted/i.test(lateRule)) return due;
  const m = /up to (\d+) days?/i.exec(lateRule);
  if (!m) return null;
  const [day, rest] = [due.slice(0, 10), due.slice(10)];
  return `${addDays(day, Number(m[1]))}${rest}`;
}

// --------------------------------------------------------------------------- a student's state

/** Where an assignment stands for one student (vocabulary.md). */
export type MyState = 'not_handed_out' | 'open' | 'late_window' | 'marking' | 'returned';

export const MY_STATE_WORD: Record<MyState, string> = {
  not_handed_out: 'not handed out', open: 'open', late_window: 'late window', marking: 'marking', returned: 'marks returned',
};

export function myState(a: SemesterAssignment, marked: boolean, now: number, tz = DEFAULT_TZ): MyState {
  if (marked) return 'returned';
  if (!a.handedOut && (!a.handout || now < instant(a.handout, tz))) return 'not_handed_out';
  if (!a.due || now < instant(a.due, tz)) return 'open';
  const cutoff = a.lateCutoff ? instant(a.lateCutoff, tz) : instant(a.due, tz);
  return now < cutoff ? 'late_window' : 'marking';
}

// --------------------------------------------------------------------------- the site source

/** A generated collection file's YAML front matter; {} when it has none or it does not parse. */
export function frontMatter(text: string): Record<string, unknown> {
  const m = /^---\r?\n([\s\S]*?)\r?\n---/.exec(text);
  if (!m) return {};
  try {
    const v = parse(m[1]);
    return v && typeof v === 'object' ? (v as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

/** `https://github.com/<org>/<repo>/(blob|tree)/<ref>/<path>` split, or null. */
export function repoPath(url: string, org: string): { repo: string; path: string } | null {
  const m = /^https:\/\/github\.com\/([^/]+)\/([^/]+)\/(?:blob|tree)\/[^/]+\/(.+)$/.exec(url);
  if (!m || m[1].toLowerCase() !== org.toLowerCase()) return null;
  return { repo: m[2], path: decodeURIComponent(m[3]) };
}

const stem = (name: string) => name.replace(/\.md$/, '');
const SHAPES: Record<string, SubmitVia> = { external: 'external', 'shared-dropbox-repo': 'shared_dropbox_repo' };

function assignmentOf(file: string, fm: Record<string, unknown>): SemesterAssignment {
  const shape = str(fm.submit_shape);
  const submitVia: SubmitVia = SHAPES[shape] ?? (shape.startsWith('assignment-repo') ? 'assignment_repo' : '');
  const due = (fm.due_event as Record<string, unknown> | undefined)?.date;
  const dueIso = due ? str(due) : null;
  const lateRule = str(fm.late_rule);
  const joins = str(fm.team_join_url) !== '';
  const cap = Number(str(fm.team_join_cap));
  return {
    slug: stem(file).replace(/^\d+-/, ''),
    title: str(fm.title),
    subtitle: str(fm.subtitle),
    handout: fm.date ? str(fm.date) : null,
    due: dueIso,
    lateCutoff: cutoffFrom(dueIso, lateRule),
    lateRule,
    cutoffSentence: str(fm.cutoff_sentence),
    submitVia,
    privateRepo: shape === 'assignment-repo-private',
    submitUrl: str(fm.submit_url),
    group: /<your-team>/.test(`${str(fm.repo_name)} ${str(fm.submit_path)}`) || joins,
    teamFormation: joins ? { closes: str(fm.team_join_closes), cap: Number.isFinite(cap) && cap > 0 ? cap : null } : null,
    solutionShown: fm.solution_datetime ? str(fm.solution_datetime) : null,
    maxPoints: str(fm.max_points),
    handedOut: fm.handout_pending !== true,
  };
}

function rowsOf(dir: string, file: string, fm: Record<string, unknown>, org: string): ScheduleRow[] {
  const id = stem(file);
  const base = {
    title: str(fm.title),
    subtitle: str(fm.subtitle),
    details: str(fm.details),
    allDay: fm.hide_time === true,
    released: fm.unreleased !== true,
    links: [] as ScheduleRow['links'],
  };
  if (dir === '_assignments') {
    const slug = id.replace(/^\d+-/, '');
    const due = fm.due_event as Record<string, unknown> | undefined;
    const out: ScheduleRow[] = [];
    if (fm.date) out.push({ ...base, id: `${id}:handout`, kind: 'assignment', when: str(fm.date), assignment: slug, details: '' });
    if (due?.date) out.push({ ...base, id: `${id}:due`, kind: 'due', when: str(due.date), assignment: slug, details: '' });
    return out;
  }
  if (!fm.date) return [];
  const links = Array.isArray(fm.links) ? (fm.links as Record<string, unknown>[]) : [];
  return [{
    ...base,
    id,
    kind: str(fm.type) || (dir === '_lectures' ? 'lecture' : 'special_event'),
    when: str(fm.date),
    links: links.map((l) => {
      const url = str(l.url);
      const rp = repoPath(url, org);
      return { name: str(l.name), url, ...(rp ?? {}) };
    }),
  }];
}

const cardsOf = (list: unknown, role: InstructorCard['role']): InstructorCard[] =>
  (Array.isArray(list) ? (list as Record<string, unknown>[]) : []).map((p) => ({
    name: str(p.name),
    title: str(p.title),
    webpage: str(p.webpage),
    picture: pictureOf(str(p.profile_pic)),
    role,
  })).filter((c) => c.name);

/** A picture the console's image policy lets through (GitHub avatars); anything else shows initials. */
function pictureOf(src: string): string {
  return /^https:\/\/(avatars\.githubusercontent\.com|github\.com)\//.test(src) ? src : '';
}

/**
 * Today's source: the public site repo `<org>/<org>.github.io`, read through the API (the page's
 * policy allows api.github.com, not github.io). The generated collections `_lectures/`,
 * `_events/`, `_assignments/` are the schedule rows; `_data/people.yml` the cards;
 * `_data/late_policy.yml` and `_data/materials.yml` the rest. One listing per collection and
 * one read per file; every read is ETag-cached, so a second visit costs no rate limit.
 */
export class SiteSource implements StudentData {
  private memo = new Map<string, { at: number; p: Promise<SemesterFacts | null> }>();

  constructor(
    private readonly client: GitHubClient,
    private readonly clock: () => number = Date.now,
  ) {}

  /** Read once per semester and kept for FRESH_MS, so moving between screens costs nothing. */
  facts(org: string): Promise<SemesterFacts | null> {
    const key = org.toLowerCase();
    const hit = this.memo.get(key);
    if (hit && this.clock() - hit.at < FRESH_MS) return hit.p;
    const p = this.read(org);
    this.memo.set(key, { at: this.clock(), p });
    p.catch(() => this.memo.delete(key));
    return p;
  }

  private async read(org: string): Promise<SemesterFacts | null> {
    const site = `${org}.github.io`;
    const c = this.client;
    const dirs = ['_lectures', '_events', '_assignments'] as const;
    const [listings, people, late, materials] = await Promise.all([
      Promise.all(dirs.map((d) => c.listDir(org, site, d))),
      c.getContents(org, site, '_data/people.yml'),
      c.getContents(org, site, '_data/late_policy.yml'),
      c.getContents(org, site, '_data/materials.yml'),
    ]);
    if (listings.every((l) => l === null) && !people) return null;
    const files = dirs.flatMap((d, i) => (listings[i] ?? []).filter((e: DirEntry) => e.type === 'file' && e.name.endsWith('.md')).map((e) => [d, e] as const));
    const texts = await Promise.all(files.map(([, e]) => c.getContents(org, site, e.path)));
    const rows: ScheduleRow[] = [];
    const assignments: SemesterAssignment[] = [];
    let archive: string | null = null;
    files.forEach(([d, e], i) => {
      const fm = frontMatter(texts[i]?.text ?? '');
      const archiveRow = d === '_events' && stem(e.name) === 'cohort-archived';
      if (archiveRow) archive = fm.date ? str(fm.date) : null;
      // The engine still titles that row with the old word; the console says semester.
      rows.push(...rowsOf(d, e.name, archiveRow ? { ...fm, title: 'Semester archived', details: '' } : fm, org));
      if (d === '_assignments') assignments.push(assignmentOf(e.name, fm));
    });
    const ppl = yamlOf(people?.text);
    const lp = yamlOf(late?.text);
    return {
      timezone: DEFAULT_TZ,
      rows,
      assignments,
      instructors: [...cardsOf(ppl.instructors, 'instructor'), ...cardsOf(ppl.teaching_assistants, 'teaching_assistant')],
      archive,
      latePolicy: (Array.isArray(lp.policies) ? lp.policies : []).map(str).filter(Boolean),
      materialsRepos: materialsReposOf(materials?.text ?? '', org),
    };
  }
}

function yamlOf(text: string | undefined): Record<string, unknown> {
  if (!text) return {};
  try {
    const v = parse(text);
    return v && typeof v === 'object' ? (v as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

/** The repos `_data/materials.yml` links into; `materials` when it names none. */
export function materialsReposOf(text: string, org: string): string[] {
  const repos = new Set<string>();
  for (const m of text.matchAll(/https:\/\/github\.com\/[^\s"']+/g)) {
    const rp = repoPath(m[0], org);
    if (rp) repos.add(rp.repo);
  }
  return repos.size ? [...repos] : ['materials'];
}

/** The rows sorted by when they happen. */
export function sortedRows(rows: ScheduleRow[], tz = DEFAULT_TZ): ScheduleRow[] {
  return [...rows].sort((a, b) => instant(a.when, tz) - instant(b.when, tz) || a.id.localeCompare(b.id));
}
