// Per-viewer settings kept in this browser (localStorage): whether Your semesters shows the
// current ones only, which All courses sections show the person's own rows only (on unless
// switched off), when the student last opened each semester (for "new since your last visit"),
// and a person's Profile (root folder, per-course folders, editor, the Open button's last
// choice), one for both roles (decision 0027). Storage can be missing or refuse (a private
// window, blocked site data); the console then shows every semester, calls nothing new, and
// keeps Profile in memory until reload.

import { EDITORS, OPEN_CHOICES, type Editor, type OpenChoice, type Setup } from './open';

export interface PrefStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem?(key: string): void;
  readonly length?: number;
  key?(index: number): string | null;
}

function localStore(): PrefStore | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
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

/**
 * On sign-out: every visit time of `login` in this browser (and the old student folders), and this load's answers. Profile
 * is kept: a folder and an editor are not secrets (decision 0021).
 */
export function forgetStudentPrefs(login: string, store: PrefStore | null = localStore()): void {
  visits.clear();
  try {
    if (!store?.key || store.length === undefined || !store.removeItem) return;
    const mine = [];
    for (let i = 0; i < store.length; i++) {
      const k = store.key(i);
      // `dsl-console-paths:` held the student folders Set up had before Profile served both
      // roles (decision 0027): removed here once, so an old browser does not keep them.
      if (k && (k.startsWith(`dsl-console-visit:${login}:`) || k === `dsl-console-paths:${login}`)) mine.push(k);
    }
    for (const k of mine) store.removeItem(k);
  } catch {
    /* storage unavailable: nothing was kept */
  }
}

const setupKey = (login: string) => `dsl-console-setup:${login}`;
/** Profile per login when storage refuses it: it then lasts until reload. */
const kept = new Map<string, Setup>();

/** Forget the in-memory copies kept when storage refused (tests). */
export const resetKeptSetups = () => kept.clear();

/** `login`'s Profile, or null when nothing is stored (and nothing kept since storage refused). */
export function yourSetup(login: string, store: PrefStore | null = localStore()): Setup | null {
  try {
    const raw = store?.getItem(setupKey(login));
    if (!raw) return kept.get(login) ?? null;
    const v = JSON.parse(raw) as Partial<Record<keyof Setup, unknown>>;
    const setup: Setup = {
      folder: typeof v.folder === 'string' ? v.folder : '',
      editor: EDITORS.includes(v.editor as Editor) ? (v.editor as Editor) : 'vscode',
    };
    if (typeof v.scheme === 'string' && v.scheme) setup.scheme = v.scheme;
    if (OPEN_CHOICES.includes(v.lastOpen as OpenChoice)) setup.lastOpen = v.lastOpen as OpenChoice;
    const overrides = v.overrides && typeof v.overrides === 'object' ? Object.entries(v.overrides).filter((e): e is [string, string] => typeof e[1] === 'string' && !!e[1].trim()) : [];
    if (overrides.length) setup.overrides = Object.fromEntries(overrides);
    return setup;
  } catch {
    return kept.get(login) ?? null;
  }
}

/** Store `setup`; false when storage refused and it is kept only until reload. */
export function saveYourSetup(login: string, setup: Setup, store: PrefStore | null = localStore()): boolean {
  try {
    if (!store) throw new Error('no storage');
    store.setItem(setupKey(login), JSON.stringify(setup));
    kept.delete(login);
    return true;
  } catch {
    kept.set(login, setup);
    return false;
  }
}

/** Keep `choice` as the Open button's default, leaving the rest of the setup as it is. */
export function rememberOpen(login: string, choice: OpenChoice, store: PrefStore | null = localStore()): Setup {
  const next: Setup = { ...(yourSetup(login, store) ?? { folder: '', editor: 'vscode' }), lastOpen: choice };
  saveYourSetup(login, next, store);
  return next;
}

// --------------------------------------------------------------------------- My courses (decision 0030 rule 3)

/** The All courses sections that carry their own My courses checkbox. */
export type CatalogueSection = 'courses' | 'now' | 'past';

/**
 * Per login per section; a section with no key is on. A new name: the `dsl-console-my-courses:`
 * keys of earlier builds hold states the person may never have chosen (one build showed every
 * section off from the page-wide key and stored each section's key from there), so they are
 * not followed (round 4).
 */
const myCoursesKey = (login: string, section: CatalogueSection) => `dsl-console-mine:${login}:${section}`;
/** The keys of earlier builds, page-wide and per section: deleted when first read, never followed. */
const oldMyCoursesKeys = (login: string) => [`dsl-console-my-courses:${login}`, ...(['courses', 'now', 'past'] as const).map((s) => `dsl-console-my-courses:${login}:${s}`)];

/** Whether `login` chose to see only their own rows in `section` of All courses; on unless that section was switched off. */
export function myCoursesOnly(login: string, section: CatalogueSection, store: PrefStore | null = localStore()): boolean {
  try {
    for (const k of oldMyCoursesKeys(login)) store?.removeItem?.(k);
    return store?.getItem(myCoursesKey(login, section)) !== '0';
  } catch {
    return true;
  }
}

export function saveMyCoursesOnly(login: string, section: CatalogueSection, on: boolean, store: PrefStore | null = localStore()): void {
  try {
    store?.setItem(myCoursesKey(login, section), on ? '1' : '0');
  } catch {
    /* storage unavailable: the choice lasts until reload */
  }
}

// --------------------------------------------------------------------------- Current only (decision 0029)

const currentKey = (login: string) => `dsl-console-current-only:${login}`;

/** Whether `login` chose to see only their current semesters on Your semesters; off by default. */
export function currentOnly(login: string, store: PrefStore | null = localStore()): boolean {
  try {
    return store?.getItem(currentKey(login)) === '1';
  } catch {
    return false;
  }
}

export function saveCurrentOnly(login: string, on: boolean, store: PrefStore | null = localStore()): void {
  try {
    store?.setItem(currentKey(login), on ? '1' : '0');
  } catch {
    /* storage unavailable: the choice lasts until reload */
  }
}
