// Instructor-owned files the read screens show directly (the roster grid, the staff table,
// the schedule's Details, a template's settings), read with the user's token and held in
// signals so a screen re-renders when its file arrives.

import { signal, type Signal } from '@preact/signals';
import type { DirEntry, GhRepo, GitHubClient } from '../github/client';

export type FileState =
  | { kind: 'loading' }
  | { kind: 'absent' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; text: string; sha: string };

export type DirState = { kind: 'loading' } | { kind: 'absent' } | { kind: 'error'; message: string } | { kind: 'ready'; entries: DirEntry[] };

/** A repo's whole tree at a ref: every path, with `dir` for folders. */
/** `truncated`: GitHub returned only part of the tree (its recursive listing has a size cap). */
export type TreeState = { kind: 'loading' } | { kind: 'absent' } | { kind: 'ready'; paths: { path: string; dir: boolean }[]; truncated: boolean };

/** An org's repos as the user sees them. */
export type ReposState = { kind: 'loading' } | { kind: 'absent' } | { kind: 'ready'; repos: GhRepo[] };

export interface Files {
  file(owner: string, repo: string, path: string, ref?: string): FileState;
  dir(owner: string, repo: string, path: string): DirState;
  member(org: string, user: string): boolean | null | undefined; // undefined while loading
  /** When a file last changed (ISO), null when that cannot be told; undefined while loading. */
  lastChange(owner: string, repo: string, path: string): string | null | undefined;
  tree(owner: string, repo: string, ref?: string): TreeState;
  repos(org: string): ReposState;
  /** Record a file the console just wrote, so every screen shows the new text and sha. */
  put(owner: string, repo: string, path: string, ref: string | undefined, text: string | null, sha: string): void;
  /** Read a file again (after a conflict), and a directory listing. */
  refresh(owner: string, repo: string, path: string, ref?: string): void;
}

/** Files read live through the client, each on first ask. */
export class LiveFiles implements Files {
  private files = new Map<string, Signal<FileState>>();
  private dirs = new Map<string, Signal<DirState>>();
  private members = new Map<string, Signal<boolean | null | undefined>>();
  private changes = new Map<string, Signal<string | null | undefined>>();
  private trees = new Map<string, Signal<TreeState>>();
  private orgRepos = new Map<string, Signal<ReposState>>();
  constructor(private readonly client: GitHubClient) {}

  private fileKey(owner: string, repo: string, path: string, ref?: string) {
    return `${owner}/${repo}/${path}@${ref ?? ''}`;
  }

  put(owner: string, repo: string, path: string, ref: string | undefined, text: string | null, sha: string): void {
    const k = this.fileKey(owner, repo, path, ref);
    const v: FileState = text === null ? { kind: 'absent' } : { kind: 'ready', text, sha };
    const s = this.files.get(k);
    if (s) s.value = v;
    else this.files.set(k, signal<FileState>(v));
    // A write is a new commit on this path: read its date again, into the same signal so
    // a screen already showing it updates.
    const c = this.changes.get(`${owner}/${repo}/${path}`);
    if (c) this.loadChange(owner, repo, path, c);
    const dir = path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '';
    const d = this.dirs.get(`${owner}/${repo}/${dir}`);
    if (d)
      this.client
        .listDir(owner, repo, dir)
        .then((e) => (d.value = e ? { kind: 'ready', entries: e } : { kind: 'absent' }))
        .catch(() => {});
  }

  refresh(owner: string, repo: string, path: string, ref?: string): void {
    this.files.delete(this.fileKey(owner, repo, path, ref));
    this.changes.delete(`${owner}/${repo}/${path}`);
    this.dirs.delete(`${owner}/${repo}/${path}`);
  }

  tree(owner: string, repo: string, ref = 'HEAD'): TreeState {
    const k = `${owner}/${repo}@${ref}`;
    let s = this.trees.get(k);
    if (!s) {
      const sig = signal<TreeState>({ kind: 'loading' });
      s = sig;
      this.trees.set(k, sig);
      this.client
        .listTree(owner, repo, ref, true)
        .then((t) => (sig.value = t ? { kind: 'ready', paths: t.tree.filter((e) => e.type !== 'commit').map((e) => ({ path: e.path, dir: e.type === 'tree' })), truncated: t.truncated === true } : { kind: 'absent' }))
        .catch(() => (sig.value = { kind: 'absent' }));
    }
    return s.value;
  }

  repos(org: string): ReposState {
    let s = this.orgRepos.get(org);
    if (!s) {
      const sig = signal<ReposState>({ kind: 'loading' });
      s = sig;
      this.orgRepos.set(org, sig);
      this.client
        .listOrgRepos(org)
        .then((repos) => (sig.value = { kind: 'ready', repos }))
        .catch(() => (sig.value = { kind: 'absent' }));
    }
    return s.value;
  }

  file(owner: string, repo: string, path: string, ref?: string): FileState {
    const k = this.fileKey(owner, repo, path, ref);
    let s = this.files.get(k);
    if (!s) {
      const sig = signal<FileState>({ kind: 'loading' });
      s = sig;
      this.files.set(k, sig);
      this.client
        .getContents(owner, repo, path, ref)
        .then((f) => (sig.value = f ? { kind: 'ready', text: f.text, sha: f.sha } : { kind: 'absent' }))
        .catch((e: unknown) => (sig.value = { kind: 'error', message: e instanceof Error ? e.message : String(e) }));
    }
    return s.value;
  }

  dir(owner: string, repo: string, path: string): DirState {
    const k = `${owner}/${repo}/${path}`;
    let s = this.dirs.get(k);
    if (!s) {
      const sig = signal<DirState>({ kind: 'loading' });
      s = sig;
      this.dirs.set(k, sig);
      this.client
        .listDir(owner, repo, path)
        .then((d) => (sig.value = d ? { kind: 'ready', entries: d } : { kind: 'absent' }))
        .catch((e: unknown) => (sig.value = { kind: 'error', message: e instanceof Error ? e.message : String(e) }));
    }
    return s.value;
  }

  member(org: string, user: string): boolean | null | undefined {
    const k = `${org}/${user}`;
    let s = this.members.get(k);
    if (!s) {
      const sig = signal<boolean | null | undefined>(undefined);
      s = sig;
      this.members.set(k, sig);
      this.client
        .isOrgMember(org, user)
        .then((v) => (sig.value = v))
        .catch(() => (sig.value = null));
    }
    return s.value;
  }

  lastChange(owner: string, repo: string, path: string): string | null | undefined {
    const k = `${owner}/${repo}/${path}`;
    let s = this.changes.get(k);
    if (!s) {
      s = signal<string | null | undefined>(undefined);
      this.changes.set(k, s);
      this.loadChange(owner, repo, path, s);
    }
    return s.value;
  }

  private loadChange(owner: string, repo: string, path: string, sig: Signal<string | null | undefined>): void {
    sig.value = undefined;
    this.client
      .lastCommitDate(owner, repo, path)
      .then((v) => (sig.value = v))
      .catch(() => (sig.value = null));
  }

  forget(): void {
    this.files.clear();
    this.changes.clear();
    this.dirs.clear();
    this.members.clear();
    this.trees.clear();
    this.orgRepos.clear();
  }
}

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
