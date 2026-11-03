// Decision 0043 rule 6: a released folder that differs from what was released. The engine
// raises one problem per release and kind (`DIVERGED_COURSE`, `DIVERGED_DELETED`,
// `DIVERGED_RELEASED`), each a `later` suggestion carrying its files per copy; the console
// reads them off status.json and words them here.

import type { Problem, Status } from './types';

export const DIVERGED_KINDS = ['DIVERGED_COURSE', 'DIVERGED_DELETED', 'DIVERGED_RELEASED'] as const;
export type DivergedKind = (typeof DIVERGED_KINDS)[number];

/** One copy's side of a divergence problem: where it is copied from and to, and its files relative to the copy. */
export interface DivergedCopy {
  source: { repo: string; path: string; branch?: string };
  dest: { repo: string; path: string; branch?: string };
  added: string[];
  changed: string[];
  deleted: string[];
  /** Released side only: files students were given that are gone from the released copy. */
  removed?: string[];
}

export type DivergedProblem = Problem & { kind: DivergedKind; diverged?: DivergedCopy[] };

export const isDiverged = (p: Problem): p is DivergedProblem => (DIVERGED_KINDS as readonly string[]).includes(p.kind ?? '');

/** The divergence problems of one release, in kind order. */
export function divergenceOf(status: Status, releaseId: string): DivergedProblem[] {
  const own = (status.problems ?? []).filter((p): p is DivergedProblem => isDiverged(p) && p.release === releaseId);
  return own.sort((a, b) => DIVERGED_KINDS.indexOf(a.kind) - DIVERGED_KINDS.indexOf(b.kind));
}

/** A copy's files, every kind. */
export const fileCount = (c: DivergedCopy) => c.added.length + c.changed.length + c.deleted.length + (c.removed?.length ?? 0);

/** How many released files the course copy no longer has: what Release again can remove. */
export const deletedCount = (list: DivergedProblem[]) =>
  list.filter((p) => p.kind === 'DIVERGED_DELETED').reduce((n, p) => n + (p.diverged ?? []).reduce((m, c) => m + c.deleted.length, 0), 0);

const ID = new RegExp(`^schedule:(.+):(${DIVERGED_KINDS.join('|')}):[0-9a-f]{8}$`);
const KIND_WORD: Record<DivergedKind, string> = {
  DIVERGED_COURSE: 'Course copy changed',
  DIVERGED_DELETED: 'Deleted in course',
  DIVERGED_RELEASED: 'Released copy edited',
};

/** "Course copy changed, lab-08" from a set-aside divergence id; null for any other id. */
export function divergedLabel(id: string): string | null {
  const m = ID.exec(id);
  return m ? `${KIND_WORD[m[2] as DivergedKind]}, ${m[1]}` : null;
}

export const isDivergedId = (id: string) => ID.test(id);
