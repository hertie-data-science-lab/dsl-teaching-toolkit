// What a student's screens know about a semester that is the same for every student: the
// schedule rows with their kinds and readings, the assignments' dates, rules, briefs and
// team lists (names and headcounts only), the instructors' cards, the home text and
// announcements, the archive date. It comes through ONE interface, `StudentData`, because its source will
// change: today it is the public site repo's generated files (`SiteSource`); once the engine
// writes `.github/.system/student-status.json` (WP-D4), a source reading that one file
// replaces it and no screen changes. The semester's own `status.json` is private
// (the semester's config repo), so a student can never read it.
//
// A student's own facts (their repos, team, receipts, gradebook) do not come from here:
// they come from GitHub directly, with the student's token (`model/mine.ts`).

import { parse } from 'yaml';
import type { DirEntry, GitHubClient } from '../github/client';
import { addDays, str } from './format';
import { DEFAULT_DEST_REPO, DEFAULT_TIMEZONE } from './policy';

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
  links: FileLink[];
  /** The date is provisional ("TBC"); the row still happens when it says. */
  tbc: boolean;
  /** The released reading files of the session (also among `links`). */
  readings: FileLink[];
  /** The session's reading list as prose (markdown), when its readings shipped one. */
  readingList: string;
  /** The session has readings planned that are not released yet. */
  readingsPending: boolean;
}

export interface FileLink {
  name: string;
  repo?: string;
  path?: string;
  url: string;
}

/** A team formed for a group assignment: its name, how many are in it and the most it may hold. Never who. */
export interface TeamRoom {
  name: string;
  members: number;
  cap: number | null;
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
  /** "<penalty> per day, up to <n> days" */
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
  /** The brief (markdown), once handed out; '' before. */
  brief: string;
  /** The shape in one word: assignment-repo-private | assignment-repo-public | assignment-repo-student-choice | shared-dropbox-repo | external. */
  shape: string;
  /** Who can read the repo handed out ("NB: ..."), once handed out. */
  shapeNote: string;
  /** The dates are provisional. */
  tbc: boolean;
  /** The teams formed so far, while team formation is open. */
  teams: TeamRoom[];
}

/** The student may make the repo public themselves after the late cutoff. */
export const STUDENT_CHOICE = 'assignment-repo-student-choice';

export interface Announcement {
  when: string;
  title: string;
  /** Markdown. */
  details: string;
}

export interface InstructorCard {
  name: string;
  title: string;
  webpage: string;
  /** An image URL the console may load, or '' (initials are shown instead). */
  picture: string;
  role: 'instructor' | 'teaching_assistant';
  /** Only where the instructor chose to show it. */
  email: string;
}

export interface SemesterFacts {
  /** "Deep Learning"; '' when the source does not say. */
  courseName: string;
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
  /** The instructors' own welcome text (markdown); '' when none. */
  homeMarkdown: string;
  /** Hand-written announcements, newest first. */
  announcements: Announcement[];
  /** The released syllabus, pinned; null when none has been released. */
  syllabus: FileLink | null;
}

/** Where a student's screens get the semester's shared facts. */
export interface StudentData {
  /** The semester's facts, or null when the source has nothing for it. */
  facts(org: string): Promise<SemesterFacts | null>;
  /** An instructor card's picture as a URL the image policy lets through (a `data:` URL for one the source hosts), or '' for initials. */
  picture(org: string, url: string): Promise<string>;
}

/** Hosts the console's image policy (`img-src`) loads from directly. */
export const IMG_HOSTS = /^https:\/\/(avatars\.githubusercontent\.com|github\.com)\//;

/** A picture hosted on the semester's own site, as `[repo, path]` in its site repo; null for anything else (or a path that does not decode). */
export function sitePicture(url: string, org: string): [string, string] | null {
  const site = `${org.toLowerCase()}.github.io`;
  const m = /^https:\/\/([^/]+)\/(.+)$/.exec(url);
  if (!m || m[1].toLowerCase() !== site) return null;
  try {
    return [site, decodeURIComponent(m[2])];
  } catch {
    return null;
  }
}

const MIME: Record<string, string> = { png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', gif: 'image/gif', webp: 'image/webp' };

function bytesToBase64(b: Uint8Array): string {
  let bin = '';
  for (let i = 0; i < b.length; i += 0x8000) bin += String.fromCharCode(...b.subarray(i, i + 0x8000));
  return btoa(bin);
}

export const DEFAULT_TZ = DEFAULT_TIMEZONE;
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

/** The late cutoff from the late rule's window ("up to <n> days"; "not accepted after the deadline" is the deadline). */
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

const ARCHIVE_ROWS = ['semester-archived', 'cohort-archived'];
const stem = (name: string) => name.replace(/\.md$/, '');
const SHAPES: Record<string, SubmitVia> = { external: 'external', 'shared-dropbox-repo': 'shared_dropbox_repo' };

function assignmentOf(file: string, fm: Record<string, unknown>, body: string): SemesterAssignment {
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
    brief: fm.handout_pending === true ? '' : briefOf(body),
    shape,
    shapeNote: str(fm.shape_note),
    tbc: fm.tbc === true,
    teams: (Array.isArray(fm.teams) ? (fm.teams as Record<string, unknown>[]) : []).map((t) => {
      const n = Number(str(t.members));
      const c = Number(str(t.cap));
      // Names and headcounts only: the site's member digests are never read.
      return { name: str(t.name), members: Number.isFinite(n) ? n : 0, cap: Number.isFinite(c) && c > 0 ? c : Number.isFinite(cap) && cap > 0 ? cap : null };
    }).filter((t) => t.name),
  };
}

/** A generated page's body without its front matter. */
export function bodyOf(text: string): string {
  const m = /^---\r?\n[\s\S]*?\r?\n---\r?\n?/.exec(text);
  return m ? text.slice(m[0].length) : text;
}

/** The brief as the assignment page carries it: its body, out of the `{% raw %}` guard the site wraps it in. */
function briefOf(body: string): string {
  const b = body.replace(/\{%-?\s*(end)?raw\s*-?%\}/g, '').trim();
  return b === 'Assignment brief.' ? '' : b;
}

function rowsOf(dir: string, file: string, fm: Record<string, unknown>, org: string): ScheduleRow[] {
  const id = stem(file);
  const base = {
    title: str(fm.title),
    subtitle: str(fm.subtitle),
    details: str(fm.details),
    allDay: fm.hide_time === true,
    released: fm.unreleased !== true,
    links: [] as FileLink[],
    tbc: fm.tbc === true,
    readings: [] as FileLink[],
    readingList: '',
    readingsPending: fm.readings_pending === true,
  };
  if (dir === '_assignments') {
    const slug = id.replace(/^\d+-/, '');
    const due = fm.due_event as Record<string, unknown> | undefined;
    const out: ScheduleRow[] = [];
    if (fm.date) out.push({ ...base, id: `${id}:handout`, kind: 'assignment', when: str(fm.date), assignment: slug });
    if (due?.date) out.push({ ...base, id: `${id}:due`, kind: 'due', when: str(due.date), assignment: slug, details: str(due.details), tbc: due.tbc === true || base.tbc });
    return out;
  }
  // A row with `show_on_site: false` is on its kind's tab only, and an undated one (a
  // folder released outside the plan) has no place on a schedule.
  if (!fm.date || fm.off_schedule === true) return [];
  const raw = Array.isArray(fm.links) ? (fm.links as Record<string, unknown>[]) : [];
  const links = raw.map((l) => {
    const url = str(l.url);
    const rp = repoPath(url, org);
    return { name: str(l.name), url, ...(rp ?? {}) };
  });
  return [{
    ...base,
    id,
    kind: str(fm.kind) || str(fm.type) || (dir === '_lectures' ? 'lecture' : 'special_event'),
    when: str(fm.date),
    links,
    readings: links.filter((_, i) => /^readings?$/.test(str(raw[i].section))),
    readingList: str(fm.reading_list).trim(),
  }];
}

const cardsOf = (list: unknown, role: InstructorCard['role'], org: string): InstructorCard[] =>
  (Array.isArray(list) ? (list as Record<string, unknown>[]) : []).map((p) => ({
    name: str(p.name),
    title: str(p.title),
    webpage: str(p.webpage),
    picture: pictureOf(str(p.profile_pic), org),
    role,
    email: str(p.email),
  })).filter((c) => c.name);

/** A card picture as an absolute URL: a site-relative one (`/_images/pp/x.jpg`) resolves against the semester's site. */
export function pictureOf(src: string, org: string): string {
  if (/^https:\/\//.test(src)) return src;
  return src.startsWith('/') ? `https://${org.toLowerCase()}.github.io${src}` : '';
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

  /** A picture on the semester's site, read from its site repo through the API (the page may not load github.io) as a data: URL; '' otherwise. */
  async picture(org: string, url: string): Promise<string> {
    if (IMG_HOSTS.test(url)) return url;
    const at = sitePicture(url, org);
    const mime = MIME[at?.[1].split('.').pop()?.toLowerCase() ?? ''];
    if (!at || !mime) return '';
    const b = await this.client.getSmallBytes(org, at[0], at[1]);
    return b ? `data:${mime};base64,${bytesToBase64(b)}` : '';
  }

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
    const dirs = ['_lectures', '_events', '_assignments', '_announcements'] as const;
    const [listings, people, late, materials, config, home] = await Promise.all([
      Promise.all(dirs.map((d) => c.listDir(org, site, d))),
      c.getContents(org, site, '_data/people.yml'),
      c.getContents(org, site, '_data/late_policy.yml'),
      c.getContents(org, site, '_data/materials.yml'),
      c.getContents(org, site, '_config.yml'),
      c.getContents(org, site, 'index.md'),
    ]);
    if (listings.slice(0, 3).every((l) => l === null) && !people) return null;
    const files = dirs.flatMap((d, i) => (listings[i] ?? []).filter((e: DirEntry) => e.type === 'file' && e.name.endsWith('.md')).map((e) => [d, e] as const));
    const texts = await Promise.all(files.map(([, e]) => c.getContents(org, site, e.path)));
    const rows: ScheduleRow[] = [];
    const assignments: SemesterAssignment[] = [];
    const announcements: Announcement[] = [];
    let archive: string | null = null;
    files.forEach(([d, e], i) => {
      const text = texts[i]?.text ?? '';
      const fm = frontMatter(text);
      if (d === '_announcements') {
        if (fm.date) announcements.push({ when: str(fm.date), title: str(fm.title), details: str(fm.details) || bodyOf(text).trim() });
        return;
      }
      // `semester-archived.md` since the rename, `cohort-archived.md` on a site synced before it.
      const archiveRow = d === '_events' && ARCHIVE_ROWS.includes(stem(e.name));
      if (archiveRow) archive = fm.date ? str(fm.date) : null;
      // An old site titles that row with the old word; the console says semester.
      const title = str(fm.title) === 'Cohort archived' ? 'Semester archived' : str(fm.title);
      rows.push(...rowsOf(d, e.name, archiveRow ? { ...fm, title, details: '' } : fm, org));
      if (d === '_assignments') assignments.push(assignmentOf(e.name, fm, bodyOf(text)));
    });
    const ppl = yamlOf(people?.text);
    const lp = yamlOf(late?.text);
    const cfg = yamlOf(config?.text);
    const mat = yamlOf(materials?.text);
    const syllabus = str(mat.syllabus);
    const sp = syllabus ? repoPath(syllabus, org) : null;
    announcements.sort((a, b) => instant(b.when) - instant(a.when));
    return {
      courseName: str(cfg.course_name),
      timezone: str(cfg.timezone) || DEFAULT_TZ,
      rows,
      assignments,
      instructors: [...cardsOf(ppl.instructors, 'instructor', org), ...cardsOf(ppl.teaching_assistants, 'teaching_assistant', org)],
      archive,
      latePolicy: (Array.isArray(lp.policies) ? lp.policies : []).map(str).filter(Boolean),
      materialsRepos: materialsReposOf(materials?.text ?? '', org),
      homeMarkdown: homeText(bodyOf(home?.text ?? ''), cfg),
      announcements,
      syllabus: syllabus ? { name: sp ? (sp.path.split('/').pop() ?? sp.path) : 'Syllabus', url: syllabus, ...(sp ?? {}) } : null,
    };
  }
}

/**
 * The site's home page (`index.md`, the instructors' own) as plain markdown: `{{ site.x }}`
 * filled from `_config.yml`, an `{% if site.x %}` block kept only when x is set, and any other
 * Liquid tag dropped. Enough for what a home page carries; the site itself is the reference.
 */
export function homeText(body: string, cfg: Record<string, unknown>): string {
  const val = (k: string) => str(cfg[k]);
  return body
    .replace(/\{%-?\s*if\s+site\.(\w+)\s*-?%\}([\s\S]*?)\{%-?\s*endif\s*-?%\}/g, (_, k: string, inner: string) => (val(k) ? inner : ''))
    .replace(/\{\{-?\s*site\.(\w+)\s*-?\}\}/g, (_, k: string) => val(k))
    .replace(/\{%[\s\S]*?%\}|\{\{[\s\S]*?\}\}/g, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
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

/** The repos `_data/materials.yml` links into; the institution's release repo when it names none. */
export function materialsReposOf(text: string, org: string): string[] {
  const repos = new Set<string>();
  for (const m of text.matchAll(/https:\/\/github\.com\/[^\s"']+/g)) {
    const rp = repoPath(m[0], org);
    if (rp) repos.add(rp.repo);
  }
  return repos.size ? [...repos] : [DEFAULT_DEST_REPO];
}

/** The rows sorted by when they happen. */
export function sortedRows(rows: ScheduleRow[], tz = DEFAULT_TZ): ScheduleRow[] {
  return [...rows].sort((a, b) => instant(a.when, tz) - instant(b.when, tz) || a.id.localeCompare(b.id));
}
