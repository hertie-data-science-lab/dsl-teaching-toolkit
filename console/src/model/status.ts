// Reads `status.json` (contracts section 3), validates it against the exported schema and,
// where a screen shows it (a semester's own pages), decides whether it is stale by comparing
// its `inputs` with one recursive tree read of the repo, read alongside it. Also
// the course overview's readings of loaded statuses (decision 0025): the problems roll-up, a
// semester's next automatic event and the recent-activity list.

import { signal, type Signal } from '@preact/signals';
import schema from '../../schemas/status.schema.json';
import type { GitHubClient, Tree } from '../github/client';
import { assignmentIdent, fmtDay, releaseIdent } from './format';
import { DEFAULT_TIMEZONE } from './policy';
import { sameHandle } from './people';
import { instant } from './student';
import type { Operation, Status } from './types';
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
 * or removed since the status was computed. `inputs` keys are full paths in the repo that
 * holds the status file (nested ones like `.system/assignments.lock.yml` included, so the tree
 * must be the recursive one); a key under `course/` names a file in the course's `.github` and
 * is compared only when `courseTree` is given. A `null` sha records an input that was absent:
 * it is unchanged while the path is still absent. A truncated tree cannot prove a path absent,
 * so a path it lacks is not counted.
 */
export function staleInputs(inputs: Record<string, string | null>, tree: Tree, courseTree?: Tree): string[] {
  const changed = (t: Tree, path: string, sha: string | null) => {
    const found = t.tree.find((e) => e.path === path)?.sha;
    if (found === undefined && t.truncated) return false;
    return (found ?? null) !== sha;
  };
  const stale: string[] = [];
  for (const [key, sha] of Object.entries(inputs)) {
    if (key.startsWith('course/')) {
      if (courseTree && changed(courseTree, key.slice('course/'.length), sha)) stale.push(key);
      continue;
    }
    if (changed(tree, key, sha)) stale.push(key);
  }
  return stale;
}

/** The status file; with `withStale`, the repo's tree is read at the same time for `stale` (else `stale` is []). */
export async function loadStatus(client: GitHubClient, owner: string, repo: string, withStale = true): Promise<Loaded> {
  try {
    const [file, tree] = await Promise.all([client.getContents(owner, repo, STATUS_PATH), withStale ? client.listTree(owner, repo, 'HEAD', true) : null]);
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
    const stale = tree ? staleInputs(status.inputs, tree) : [];
    return { kind: 'ready', status, sha: file.sha, stale };
  } catch (e) {
    return { kind: 'error', message: e instanceof Error ? e.message : String(e) };
  }
}

/**
 * One signal per status file, loaded on first ask and reloaded on demand. Staleness costs a
 * tree read, so it is read only once a screen that shows it asks (`withStale`); a file first
 * loaded without it is read again with it then, and its reloads keep it.
 */
export class StatusStore {
  private signals = new Map<string, Signal<Loaded>>();
  private stale = new Set<string>();
  constructor(private readonly client: GitHubClient) {}

  private key(owner: string, repo: string) {
    return `${owner}/${repo}`;
  }

  get(owner: string, repo: string, withStale = true): Signal<Loaded> {
    const k = this.key(owner, repo);
    let s = this.signals.get(k);
    const upgrade = withStale && !this.stale.has(k);
    if (withStale) this.stale.add(k);
    if (!s) {
      s = signal<Loaded>({ kind: 'loading' });
      this.signals.set(k, s);
      void this.reload(owner, repo);
    } else if (upgrade) void this.reload(owner, repo);
    return s;
  }

  /** The semester's private status, in its config repo; `withStale` false for a screen that does not show staleness (Home, the course pages). */
  cohort(org: string, withStale = true): Signal<Loaded> {
    return this.get(org, CONFIG_REPO, withStale);
  }

  /** The course's public status, in `.github` (counts only); no screen shows its staleness. */
  course(org: string): Signal<Loaded> {
    return this.get(org, COURSE_REPO, false);
  }

  forget(): void {
    this.signals.clear();
    this.stale.clear();
  }

  async reload(owner: string, repo: string): Promise<void> {
    const k = this.key(owner, repo);
    const s = this.signals.get(k) ?? signal<Loaded>({ kind: 'loading' });
    this.signals.set(k, s);
    s.value = await loadStatus(this.client, owner, repo, this.stale.has(k));
  }
}

// --------------------------------------------------------------------------- course overview

/** One automatic event: what ("Assignment 2"), the event word ("hand out") and when. */
export interface NextEvent {
  title: string;
  word: string;
  when: string;
}

/**
 * The semester's next automatic event after `now`: a planned release, the hand out of an
 * assignment not handed out yet, a solution shown (when the engine holds it, the held date),
 * or the archive. Null when none is scheduled. A release automation will skip is not
 * one; marks expected is not in the status, so it is not one either.
 */
export function nextEvent(s: Status, now: number): NextEvent | null {
  const tz = s.semester?.timezone ?? DEFAULT_TIMEZONE;
  const all: NextEvent[] = [];
  for (const r of s.releases ?? []) if (r.state === 'planned' && r.when) all.push({ title: releaseIdent(r), word: 'release', when: r.when });
  for (const a of s.assignments ?? []) {
    const title = assignmentIdent(a.slug, a.title, a.number);
    if (a.handout && (a.state === 'declared' || a.state === 'teams_forming')) all.push({ title, word: 'hand out', when: a.handout });
    // Before the late cutoff the engine holds the solution until then.
    const shown = a.solution_held_until ?? a.solution_shown;
    if (shown) all.push({ title, word: 'solution shown', when: shown });
  }
  if (s.semester?.archive_date) all.push({ title: '', word: 'archive', when: s.semester.archive_date });
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
  return sameHandle(actor, login) ? 'you' : actor;
}
