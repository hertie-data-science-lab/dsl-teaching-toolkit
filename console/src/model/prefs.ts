// Per-viewer settings kept in this browser (localStorage): which semesters the student
// groups show, when the student last opened each semester (for "new since your last visit"),
// and the local folders the Set up screen writes its commands for. Storage can be missing or
// refuse (a private window, blocked site data); the console then shows every semester, calls
// nothing new, and forgets the folders on reload.

export interface PrefStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem?(key: string): void;
  readonly length?: number;
  key?(index: number): string | null;
}

const hiddenKey = (login: string) => `dsl-console-hidden-semesters:${login}`;

function localStore(): PrefStore | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

/** The semester orgs `login` chose not to show. */
export function hiddenSemesters(login: string, store: PrefStore | null = localStore()): Set<string> {
  try {
    const v: unknown = JSON.parse(store?.getItem(hiddenKey(login)) ?? '[]');
    return new Set(Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : []);
  } catch {
    return new Set();
  }
}

export function saveHiddenSemesters(login: string, hidden: Set<string>, store: PrefStore | null = localStore()): void {
  try {
    store?.setItem(hiddenKey(login), JSON.stringify([...hidden].sort()));
  } catch {
    /* storage unavailable: the choice lasts until reload */
  }
}

const visitKey = (login: string, org: string) => `dsl-console-visit:${login}:${org.toLowerCase()}`;
const visits = new Map<string, number | null>();

/**
 * When `login` last opened `org` before this page load (epoch ms), or null for a first visit.
 * Read once per page load and kept, so a line new since the last visit stays shown all load
 * even after `markVisit` stores the new time.
 */
export function lastVisit(login: string, org: string, store: PrefStore | null = localStore()): number | null {
  const key = visitKey(login, org);
  if (visits.has(key)) return visits.get(key)!;
  let before: number | null = null;
  try {
    const v = Number(store?.getItem(key) ?? '');
    before = Number.isFinite(v) && v > 0 ? v : null;
  } catch {
    /* storage unavailable: every visit is a first one */
  }
  visits.set(key, before);
  return before;
}

/** Store `now` as `login`'s visit to `org`: called only once the receipts it compares against have been read, so a failed read does not hide a note next time. */
export function markVisit(login: string, org: string, now: number, store: PrefStore | null = localStore()): void {
  lastVisit(login, org, store); // keep this load's answer first
  try {
    store?.setItem(visitKey(login, org), String(now));
  } catch {
    /* storage unavailable */
  }
}

/** Forget this page load's answers (tests). */
export const resetVisits = () => visits.clear();

/** On sign-out: every visit time and remembered folder of `login` in this browser, and this load's answers. */
export function forgetStudentPrefs(login: string, store: PrefStore | null = localStore()): void {
  visits.clear();
  try {
    if (!store?.key || store.length === undefined || !store.removeItem) return;
    const mine = [];
    for (let i = 0; i < store.length; i++) {
      const k = store.key(i);
      if (k && (k.startsWith(`dsl-console-visit:${login}:`) || k === pathsKey(login))) mine.push(k);
    }
    for (const k of mine) store.removeItem(k);
  } catch {
    /* storage unavailable: nothing was kept */
  }
}

/** The local folders a student keeps their clones in. */
export interface LocalPaths {
  materials: string;
  assignments: string;
}

const pathsKey = (login: string) => `dsl-console-paths:${login}`;

export function localPaths(login: string, store: PrefStore | null = localStore()): LocalPaths {
  try {
    const v = JSON.parse(store?.getItem(pathsKey(login)) ?? '{}') as Partial<LocalPaths>;
    return { materials: typeof v.materials === 'string' ? v.materials : '', assignments: typeof v.assignments === 'string' ? v.assignments : '' };
  } catch {
    return { materials: '', assignments: '' };
  }
}

export function saveLocalPaths(login: string, paths: LocalPaths, store: PrefStore | null = localStore()): void {
  try {
    store?.setItem(pathsKey(login), JSON.stringify(paths));
  } catch {
    /* storage unavailable: the folders last until reload */
  }
}
