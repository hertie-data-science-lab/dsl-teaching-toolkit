// Instructor-owned files the read screens show directly (the roster grid, the staff table,
// the schedule's Details, a template's settings), read with the user's token and held in
// signals so a screen re-renders when its file arrives.

import { signal, type Signal } from '@preact/signals';
import type { DirEntry, GitHubClient } from '../github/client';

export type FileState =
  | { kind: 'loading' }
  | { kind: 'absent' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; text: string; sha: string };

export type DirState = { kind: 'loading' } | { kind: 'absent' } | { kind: 'ready'; entries: DirEntry[] };

export interface Files {
  file(owner: string, repo: string, path: string, ref?: string): FileState;
  dir(owner: string, repo: string, path: string): DirState;
  member(org: string, user: string): boolean | null | undefined; // undefined while loading
}

/** Files read live through the client, each on first ask. */
export class LiveFiles implements Files {
  private files = new Map<string, Signal<FileState>>();
  private dirs = new Map<string, Signal<DirState>>();
  private members = new Map<string, Signal<boolean | null | undefined>>();
  constructor(private readonly client: GitHubClient) {}

  file(owner: string, repo: string, path: string, ref?: string): FileState {
    const k = `${owner}/${repo}/${path}@${ref ?? ''}`;
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
        .catch(() => (sig.value = { kind: 'absent' }));
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

  forget(): void {
    this.files.clear();
    this.dirs.clear();
    this.members.clear();
  }
}

/** A fixed set of files, for render tests. Keys are `owner/repo/path`. */
export class StaticFiles implements Files {
  constructor(
    private readonly map: Record<string, string> = {},
    private readonly dirMap: Record<string, string[]> = {},
  ) {}
  file(owner: string, repo: string, path: string): FileState {
    const t = this.map[`${owner}/${repo}/${path}`];
    return t === undefined ? { kind: 'absent' } : { kind: 'ready', text: t, sha: 'static' };
  }
  dir(owner: string, repo: string, path: string): DirState {
    const d = this.dirMap[`${owner}/${repo}/${path}`];
    return d ? { kind: 'ready', entries: d.map((name) => ({ name, path: `${path}/${name}`, sha: 'static', type: 'file' })) } : { kind: 'absent' };
  }
  member(): boolean | null {
    return true;
  }
}
