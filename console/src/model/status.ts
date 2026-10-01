// Reads `status.json` (contracts section 3), validates it against the exported schema and
// decides whether it is stale by comparing its `inputs` with one tree read of the repo. Also
// the course overview's readings of loaded statuses (decision 0025): the problems roll-up, a
// semester's next automatic event and the recent-activity list.

import { signal, type Signal } from '@preact/signals';
import schema from '../../schemas/status.schema.json';
import type { GitHubClient, Tree } from '../github/client';
import { assignmentIdent, fmtDay, releaseIdent } from './format';
import { DEFAULT_TIMEZONE } from './policy';
import { instant } from './student';
import type { Operation, Problem, Status } from './types';
import { CONFIG_REPO, COURSE_REPO, STATUS_PATH } from './names';
import { validator } from './validate';

export { STATUS_PATH };

export type Loaded =
  | { kind: 'loading' }
  | { kind: 'absent' } // not computed yet: the org has not been refreshed on the new engine
  | { kind: 'invalid'; errors: string[] }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; status: Status; sha: string; stale: string[] };

const validStatus = validator(schema);

export function validateStatus(data: unknown): string[] {
  if (validStatus(data)) return [];
  return (validStatus.errors ?? []).map((e) => `${e.instancePath || '/'} ${e.message ?? 'is invalid'}`);
}

/**
 * The inputs whose sha no longer matches the tree: a file edited, added under a tracked name
 * or removed since the status was computed. `inputs` keys are paths in the repo that holds the
 * status file; a key under `course/` names a file in the course's `.github` and is compared
 * only when `courseTree` is given (a semester read makes one tree read, of its own repo).
 */
export function staleInputs(inputs: Record<string, string>, tree: Tree, courseTree?: Tree): string[] {
  const shaOf = (t: Tree, path: string) => t.tree.find((e) => e.path === path)?.sha;
  const stale: string[] = [];
  for (const [key, sha] of Object.entries(inputs)) {
    if (key.startsWith('course/')) {
      if (!courseTree) continue;
      if (shaOf(courseTree, key.slice('course/'.length)) !== sha) stale.push(key);
      continue;
    }
    if (shaOf(tree, key) !== sha) stale.push(key);
  }
  return stale;
}

export async function loadStatus(client: GitHubClient, owner: string, repo: string): Promise<Loaded> {
  try {
    const file = await client.getContents(owner, repo, STATUS_PATH);
    if (!file) return { kind: 'absent' };
    let data: unknown;
    try {
      data = JSON.parse(file.text);
    } catch {
      return { kind: 'invalid', errors: ['status.json is not valid JSON'] };
    }
    const errors = validateStatus(data);
    if (errors.length) return { kind: 'invalid', errors };
    const status = data as Status;
    const tree = await client.listTree(owner, repo, 'HEAD');
    const stale = tree ? staleInputs(status.inputs, tree) : [];
    return { kind: 'ready', status, sha: file.sha, stale };
  } catch (e) {
    return { kind: 'error', message: e instanceof Error ? e.message : String(e) };
  }
}

/** One signal per status file, loaded on first ask and reloaded on demand. */
export class StatusStore {
  private signals = new Map<string, Signal<Loaded>>();
  constructor(private readonly client: GitHubClient) {}

  private key(owner: string, repo: string) {
    return `${owner}/${repo}`;
  }

  get(owner: string, repo: string): Signal<Loaded> {
    const k = this.key(owner, repo);
    let s = this.signals.get(k);
    if (!s) {
      s = signal<Loaded>({ kind: 'loading' });
      this.signals.set(k, s);
      void this.reload(owner, repo);
    }
    return s;
  }

  /** The semester's private status, in its config repo. */
  cohort(org: string): Signal<Loaded> {
    return this.get(org, CONFIG_REPO);
  }

  /** The course's public status, in `.github` (counts only). */
  course(org: string): Signal<Loaded> {
    return this.get(org, COURSE_REPO);
  }

  forget(): void {
    this.signals.clear();
  }

  async reload(owner: string, repo: string): Promise<void> {
    const s = this.signals.get(this.key(owner, repo)) ?? signal<Loaded>({ kind: 'loading' });
    this.signals.set(this.key(owner, repo), s);
    s.value = await loadStatus(this.client, owner, repo);
  }
}

// --------------------------------------------------------------------------- course overview

/** A problem on the course overview; a semester's carries that semester, for its tag and links. */
export type TaggedProblem = Problem & { semester?: { org: string; label: string } };

/**
 * The overview's Problems: the course's first, then each live semester's, tagged. A semester
 * repeats the course faults it will pay for (`scope: course`); one already listed is left out.
 */
export function rollUpProblems(course: Problem[], semesters: { org: string; label: string; problems: Problem[] }[]): TaggedProblem[] {
  const seen = new Set(course.map((p) => p.id));
  const own = semesters.flatMap((s) =>
    s.problems.filter((p) => !(p.scope === 'course' && seen.has(p.id))).map((p) => ({ ...p, semester: { org: s.org, label: s.label } })),
  );
  return [...course, ...own];
}

/** One automatic event: what ("Assignment 2"), the event word ("hand out") and when. */
export interface NextEvent {
  title: string;
  word: string;
  when: string;
}

/**
 * The semester's next automatic event after `now`: a planned release, a hand out, a solution
 * shown, or the archive. Null when none is scheduled. A release automation will skip is not
 * one; marks expected is not in the status, so it is not one either.
 */
export function nextEvent(s: Status, now: number): NextEvent | null {
  const tz = s.semester?.timezone ?? DEFAULT_TIMEZONE;
  const all: NextEvent[] = [];
  for (const r of s.releases ?? []) if (r.state === 'planned') all.push({ title: releaseIdent(r), word: 'release', when: r.when });
  for (const a of s.assignments ?? []) {
    const title = assignmentIdent(a.slug, a.title, a.number);
    if (a.handout) all.push({ title, word: 'hand out', when: a.handout });
    if (a.solution_shown) all.push({ title, word: 'solution shown', when: a.solution_shown });
  }
  if (s.semester?.archive_date) all.push({ title: '', word: 'Archive', when: s.semester.archive_date });
  const ahead = all.map((e) => ({ e, at: instant(e.when, tz) })).filter((x) => x.at > now).sort((a, b) => a.at - b.at);
  return ahead[0]?.e ?? null;
}

/** "Next: Assignment 2 hand out, Mon 6 Oct", or "Nothing scheduled". */
export function nextEventWords(e: NextEvent | null, tz?: string, refYear?: number): string {
  if (!e) return 'Nothing scheduled';
  return `Next: ${[e.title, e.word].filter(Boolean).join(' ')}, ${fmtDay(e.when, tz, refYear)}`;
}

/** One line of the overview's Recent activity: an operation, where it ran, and who ran it. */
export interface Activity extends Operation {
  /** The semester org it ran on; absent for a course operation. */
  org?: string;
  /** The semester's name, or "course". */
  where: string;
  /** The login that started it; '' for a scheduled run; undefined when not known. */
  actor?: string;
}

/** The newest `n` operations of every list, each run once (the first list naming it wins). */
export function recentActivity(lists: Activity[][], n = 5): Activity[] {
  const seen = new Set<number>();
  const out: Activity[] = [];
  for (const a of lists.flat()) {
    if (seen.has(a.run_id)) continue;
    seen.add(a.run_id);
    out.push(a);
  }
  return out.sort((a, b) => (b.finished ?? '').localeCompare(a.finished ?? '')).slice(0, n);
}

/** Who ran an operation: "you", "automation" for a scheduled run, else the login; null when not known. */
export function whoWord(actor: string | undefined, login: string): string | null {
  if (actor === undefined) return null;
  if (!actor) return 'automation';
  return actor.toLowerCase() === login.toLowerCase() ? 'you' : actor;
}
