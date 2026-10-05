// A fixed set of files for render tests: the `Files` the screens read, with no GitHub behind it.

import type { GhRepo } from '../src/github/client';
import type { DirState, FileState, Files, ReposState, TreeState } from '../src/model/files';

/** A fixed set of files, for render tests. Keys are `owner/repo/path`. */
export class StaticFiles implements Files {
  constructor(
    private readonly map: Record<string, string> = {},
    /** A listing, or an Error for one that could not be read. */
    private readonly dirMap: Record<string, string[] | Error> = {},
    private readonly treeMap: Record<string, string[]> = {},
    private readonly repoMap: Record<string, Partial<GhRepo>[]> = {},
    private readonly changeMap: Record<string, string> = {},
  ) {}
  lastChange(owner: string, repo: string, path: string): string | null {
    return this.changeMap[`${owner}/${repo}/${path}`] ?? null;
  }
  repos(org: string): ReposState {
    const r = this.repoMap[org];
    return r ? { kind: 'ready', repos: r.map((x) => ({ full_name: `${org}/${x.name}`, private: true, default_branch: 'main', html_url: `https://github.com/${org}/${x.name}`, name: '', ...x })) } : { kind: 'absent' };
  }
  tree(owner: string, repo: string): TreeState {
    const t = this.treeMap[`${owner}/${repo}`];
    if (!t) return { kind: 'absent' };
    const dirs = new Set<string>();
    for (const p of t) p.split('/').slice(0, -1).forEach((_, i, a) => dirs.add(a.slice(0, i + 1).join('/')));
    return { kind: 'ready', paths: [...[...dirs].map((path) => ({ path, dir: true })), ...t.map((path) => ({ path, dir: false }))], truncated: false };
  }
  put(owner: string, repo: string, path: string, _ref: string | undefined, text: string | null): void {
    if (text === null) delete this.map[`${owner}/${repo}/${path}`];
    else this.map[`${owner}/${repo}/${path}`] = text;
  }
  refresh(): void {}
  file(owner: string, repo: string, path: string): FileState {
    const t = this.map[`${owner}/${repo}/${path}`];
    return t === undefined ? { kind: 'absent' } : { kind: 'ready', text: t, sha: 'static' };
  }
  dir(owner: string, repo: string, path: string): DirState {
    const d = this.dirMap[`${owner}/${repo}/${path}`];
    if (d instanceof Error) return { kind: 'error', message: d.message };
    return d ? { kind: 'ready', entries: d.map((name) => ({ name, path: `${path}/${name}`, sha: 'static', type: 'file' })) } : { kind: 'absent' };
  }
  member(): boolean | null {
    return true;
  }
}
