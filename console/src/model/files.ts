// Instructor-owned files the read screens show directly (the roster grid, the instructors table,
// the schedule's Details, a template's settings), read with the user's token and held in
// signals so a screen re-renders when its file arrives. A read that fails is an `error`, never
// "absent": it is read again on the first ask once RETRY_MS has passed (the clock's minute tick
// re-renders every screen), and the screen says it could not read meanwhile.

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
export type TreeState = { kind: 'loading' } | { kind: 'absent' } | { kind: 'error'; message: string } | { kind: 'ready'; paths: { path: string; dir: boolean }[]; truncated: boolean };

/** An org's repos as the user sees them. */
export type ReposState = { kind: 'loading' } | { kind: 'error'; message: string } | { kind: 'ready'; repos: GhRepo[] };

/** How long a failed read stands before an ask reads it again. */
export const RETRY_MS = 30_000;

const errorOf = (e: unknown) => ({ kind: 'error' as const, message: e instanceof Error ? e.message : String(e) });

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
  /** When each failed read failed. */
  private failedAt = new WeakMap<Signal<unknown>, number>();
  constructor(
    private readonly client: GitHubClient,
    private readonly clock: () => number = Date.now,
  ) {}

  /** `k`'s state, read on the first ask, and again on an ask RETRY_MS after a failed read. */
  private ask<S extends { kind: string }>(cache: Map<string, Signal<S>>, k: string, read: () => Promise<S>): S {
    let s = cache.get(k);
    const at = s && this.failedAt.get(s);
    if (!s || (at !== undefined && this.clock() - at >= RETRY_MS)) {
      const sig = signal<S>({ kind: 'loading' } as S);
      s = sig;
      cache.set(k, sig);
      read().then(
        (v) => (sig.value = v),
        (e: unknown) => {
          this.failedAt.set(sig, this.clock());
          sig.value = errorOf(e) as unknown as S;
        },
      );
    }
    return s.value;
  }

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
    this.dirs.delete(`${owner}/${repo}/${path}`);
    // The date is read again into the same signal, keeping the old one until it answers: no flicker.
    const c = this.changes.get(`${owner}/${repo}/${path}`);
    if (c) this.client.lastCommitDate(owner, repo, path).then((v) => (c.value = v), () => {});
  }

  tree(owner: string, repo: string, ref = 'HEAD'): TreeState {
    return this.ask(this.trees, `${owner}/${repo}@${ref}`, async (): Promise<TreeState> => {
      const t = await this.client.listTree(owner, repo, ref, true);
      return t ? { kind: 'ready', paths: t.tree.filter((e) => e.type !== 'commit').map((e) => ({ path: e.path, dir: e.type === 'tree' })), truncated: t.truncated === true } : { kind: 'absent' };
    });
  }

  repos(org: string): ReposState {
    return this.ask(this.orgRepos, org, async (): Promise<ReposState> => ({ kind: 'ready', repos: await this.client.listOrgRepos(org) }));
  }

  file(owner: string, repo: string, path: string, ref?: string): FileState {
    return this.ask(this.files, this.fileKey(owner, repo, path, ref), async (): Promise<FileState> => {
      const f = await this.client.getContents(owner, repo, path, ref);
      return f ? { kind: 'ready', text: f.text, sha: f.sha } : { kind: 'absent' };
    });
  }

  dir(owner: string, repo: string, path: string): DirState {
    return this.ask(this.dirs, `${owner}/${repo}/${path}`, async (): Promise<DirState> => {
      const d = await this.client.listDir(owner, repo, path);
      return d ? { kind: 'ready', entries: d } : { kind: 'absent' };
    });
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
