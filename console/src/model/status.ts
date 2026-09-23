// Reads `status.json` (contracts section 3), validates it against the exported schema and
// decides whether it is stale by comparing its `inputs` with one tree read of the repo.

import { signal, type Signal } from '@preact/signals';
import schema from '../../schemas/status.schema.json';
import type { GitHubClient, Tree } from '../github/client';
import type { Status } from './types';
import { validator } from './validate';

export const STATUS_PATH = '.dsl/status.json';

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
 * only when `courseTree` is given (a cohort read makes one tree read, of its own repo).
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

  /** The cohort's private status, in `classroom-config`. */
  cohort(org: string): Signal<Loaded> {
    return this.get(org, 'classroom-config');
  }

  /** The course's public status, in `.github` (counts only). */
  course(org: string): Signal<Loaded> {
    return this.get(org, '.github');
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
