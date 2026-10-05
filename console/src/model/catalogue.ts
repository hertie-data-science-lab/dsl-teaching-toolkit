// The institution's catalogue (decision 0021 rule 5): every course org the toolkit's public
// `orgs.yml` names, each read off its public `.github` (`dsl-course.yml` for the name and
// code, `semesters.yml` for its semesters). A course in the person's estate is theirs and is
// not read again. Everything here is public: a semester's dates come from its `.github`
// (archived or not) and, while it runs, its public student status (the archive date as its end;
// without one, an approximate end from the semester key).

import { parse } from 'yaml';
import type { GitHubClient } from '../github/client';
import { CENTRAL } from './central';
import { COURSE_META_PATH, parseRegistry, REGISTRY_PATH, termOf, type Course } from './discovery';
import { addDays, str } from './format';
import { DEFAULT_TIMEZONE, ORG_NAME_RE } from './policy';
import { instant } from './student';
import { COURSE_REPO, STUDENT_STATUS_PATH } from './names';

const ORGS_PATH = 'orgs.yml';
/**
 * The toolkit ref whose `orgs.yml` this console reads: the one it was built from
 * (`VITE_TOOLKIT_REF`, set by the Pages build), so a console built from a branch reads that
 * branch's registry; `main` otherwise.
 */
export const REGISTRY_REF: string = (import.meta.env as { VITE_TOOLKIT_REF?: string } | undefined)?.VITE_TOOLKIT_REF || CENTRAL.ref;
/** Most catalogue reads in flight at once. */
const POOL = 6;

type Run = <T>(task: () => Promise<T>) => Promise<T>;

/**
 * At most `size` tasks running at once; the rest wait their turn. A finished task hands its
 * slot straight to the next in line, so a newcomer cannot take it meanwhile. Each task is one
 * request, so none waits on another.
 */
export function pool(size: number): Run {
  let active = 0;
  const queue: (() => void)[] = [];
  return async (task) => {
    if (active < size) active++;
    else await new Promise<void>((go) => queue.push(go));
    try {
      return await task();
    } finally {
      const next = queue.shift();
      if (next) next();
      else active--;
    }
  };
}

export interface CatalogueSemester {
  org: string;
  termLabel: string;
  start?: string;
  end?: string;
  /** Unknown (absent) when the semester's `.github` could not be read. */
  archived?: boolean;
}

export interface CatalogueCourse {
  org: string;
  name: string;
  code: string;
  semesters: CatalogueSemester[];
  mine: boolean;
  /** The person's own course, when `mine`. */
  course?: Course;
}

/** `orgs.yml`'s course orgs as spelt, in order (`dsl_course/org_registry.parse_names`); throws on any other shape. */
export function parseOrgs(text: string): string[] {
  const doc: unknown = parse(text);
  const orgs = doc && typeof doc === 'object' && !Array.isArray(doc) ? (doc as { course_orgs?: unknown }).course_orgs : undefined;
  if (!Array.isArray(orgs) || !orgs.every((o) => typeof o === 'string' && ORG_NAME_RE.test(o))) throw new Error('orgs.yml must be `course_orgs:` and a list of org names');
  return orgs as string[];
}

/** A semester the person cannot see, by its public `.github`; null when it has none (not set up yet). */
async function readSemester(client: GitHubClient, run: Run, org: string): Promise<CatalogueSemester | null> {
  const termLabel = termOf(org).label;
  const repo = await run(() => client.getRepo(org, COURSE_REPO)).catch(() => undefined);
  if (repo === null) return null;
  if (!repo) return { org, termLabel };
  if (repo.archived) return { org, termLabel, archived: true };
  const end = await run(() => client.getContents(org, COURSE_REPO, STUDENT_STATUS_PATH))
    .then((f) => {
      const v = f ? (JSON.parse(f.text) as { archive_datetime?: unknown } | null)?.archive_datetime : undefined;
      return typeof v === 'string' ? v : '';
    })
    .catch(() => '');
  return { org, termLabel, archived: false, ...(end ? { end } : {}) };
}

/** A course org the person has no role in; its org name alone when its `.github` cannot be read. */
async function readCourse(client: GitHubClient, run: Run, org: string): Promise<CatalogueCourse> {
  const [d, registry] = await Promise.all([
    run(() => client.getContents(org, COURSE_REPO, COURSE_META_PATH))
      .then((f) => {
        const m: unknown = f ? parse(f.text) : null;
        return m && typeof m === 'object' ? (m as Record<string, unknown>) : {};
      })
      .catch(() => ({}) as Record<string, unknown>),
    // Its own read: a registry that fails leaves the course named, with no semesters.
    run(() => client.getContents(org, COURSE_REPO, REGISTRY_PATH)).catch(() => null),
  ]);
  const semesters = (await Promise.all(parseRegistry(registry?.text).map((s) => readSemester(client, run, s)))).filter((s): s is CatalogueSemester => s !== null);
  return { org, name: str(d.course_name) || org, code: str(d.course_code), semesters, mine: false };
}

const mineOf = (course: Course): CatalogueCourse => ({
  org: course.org,
  name: course.name,
  code: course.code,
  semesters: course.cohorts.map((c) => ({ org: c.org, termLabel: c.termLabel })),
  mine: true,
  course,
});

interface Cache {
  orgs: Promise<string[]>;
  courses: Map<string, Promise<CatalogueCourse>>;
  run: Run;
}

/** One read per org per page load, whoever asks. */
const caches = new WeakMap<GitHubClient, Cache>();

/**
 * The catalogue: the person's courses (`estate`) and every other course `orgs.yml` names.
 * `onUpdate` is called with the list so far each time a course arrives, the person's own
 * first. Rejects only when `orgs.yml` itself cannot be read; one failed course is its org name.
 */
export async function loadCatalogue(client: GitHubClient, estate: Course[] = [], onUpdate?: (list: CatalogueCourse[]) => void): Promise<CatalogueCourse[]> {
  let cache = caches.get(client);
  if (!cache) {
    const orgs = client.getContents(CENTRAL.owner, CENTRAL.repo, ORGS_PATH, REGISTRY_REF).then((f) => {
      if (!f) throw new Error('orgs.yml is missing');
      return parseOrgs(f.text);
    });
    cache = { orgs, courses: new Map(), run: pool(POOL) };
    caches.set(client, cache);
    orgs.catch(() => caches.delete(client)); // a failed read is tried again on the next load
  }
  const list = estate.map(mineOf);
  const known = new Set(list.map((c) => c.org.toLowerCase()));
  onUpdate?.([...list]);
  const others = (await cache.orgs).filter((o) => !known.has(o.toLowerCase()));
  const c = cache;
  await Promise.all(
    others.map(async (org) => {
      let p = c.courses.get(org.toLowerCase());
      if (!p) c.courses.set(org.toLowerCase(), (p = readCourse(client, c.run, org)));
      list.push(await p);
      onUpdate?.([...list]);
    }),
  );
  return list;
}

/** A semester key (`f2026`) as its season and year; null for anything else. */
export function seasonOf(term: string): { season: string; year: number } | null {
  const m = /^([fswu])(\d{4})$/.exec(term);
  return m ? { season: m[1], year: Number(m[2]) } : null;
}

/**
 * A semester's end: its archive date when read, else an approximate last day from its key
 * (fall to 31 January, spring to 31 July, summer to 30 September, winter to 31 March), so a
 * semester whose status gives no date does not run for ever.
 */
function endOf(s: CatalogueSemester): string | undefined {
  if (s.end) return s.end;
  const k = seasonOf(termOf(s.org).term);
  if (!k) return undefined;
  const { season, year } = k;
  return { f: `${year + 1}-01-31`, s: `${year}-07-31`, u: `${year}-09-30`, w: `${year + 1}-03-31` }[season];
}

/**
 * Whether a semester whose end is `end` is over at `now`: a day (yyyy-mm-dd, its last day) is
 * over once the next day starts in `tz`, as the engine's `ended` (`semester_end < today`); a
 * datetime (an archive date) is over from that instant. The one rule for "is it over".
 */
export function over(end: string, now: number, tz = DEFAULT_TIMEZONE): boolean {
  const day = end.trim();
  return (/^\d{4}-\d{2}-\d{2}$/.test(day) ? instant(addDays(day, 1), tz) : Date.parse(day)) <= now;
}

/** Whether a semester runs at `now`: not archived, and its end (read or approximate) not past; with no end at all, only when known to be open. */
export function runningNow(s: CatalogueSemester, now: number): boolean {
  if (s.archived) return false;
  const end = endOf(s);
  return end ? !over(end, now) : s.archived === false;
}

/** Whether a semester has ended by `now`: archived, or its end (read or approximate) past. */
export function endedNow(s: CatalogueSemester, now: number, tz?: string): boolean {
  if (s.archived) return true;
  const end = endOf(s);
  return !!end && over(end, now, tz);
}

/**
 * Whether a semester the person studies in is over (decision 0031): archived; else past `end`
 * (its `semester_end`, the last day, in `tz`) when known; else past its key's approximate end.
 * The one rule for Your semesters, the student nav, the banner and the landing; a loaded
 * status's `semester.ended` is preferred where there is one.
 */
export function semesterOver(s: { org: string; termLabel: string; archived: boolean }, now: number, end?: string, tz?: string): boolean {
  return endedNow({ org: s.org, termLabel: s.termLabel, archived: s.archived, ...(end ? { end } : {}) }, now, tz);
}

const SEASON_ORDER: Record<string, number> = { s: 1, u: 2, f: 3, w: 4 };

/** A semester org's (or a bare key's) place in time: larger is newer; 0 for a name without a key. */
export function termRank(org: string): number {
  const k = seasonOf(termOf(org).term);
  return k ? k.year * 10 + SEASON_ORDER[k.season] : 0;
}
