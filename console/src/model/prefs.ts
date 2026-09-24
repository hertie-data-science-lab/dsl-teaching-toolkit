// Per-viewer settings kept in this browser (localStorage): which semesters the student
// groups show. Storage can be missing or refuse (a private window, blocked site data); the
// console then shows every semester and forgets the choice on reload.

export interface PrefStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
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
