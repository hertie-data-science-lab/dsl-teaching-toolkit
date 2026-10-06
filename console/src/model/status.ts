// Reads `status.json` (contracts section 3), validates it against the exported schema and
// decides whether it is stale by comparing its `inputs` with one recursive tree read of the repo.

import { signal, type Signal } from '@preact/signals';
import schema from '../../schemas/status.schema.json';
import type { GitHubClient, Tree } from '../github/client';
import type { Status } from './types';
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
    const tree = await client.listTree(owner, repo, 'HEAD', true);
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
