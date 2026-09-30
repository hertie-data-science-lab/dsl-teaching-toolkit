// The folder check (decision 0023): where the browser has the File System Access API (Chrome,
// Edge), the person picks their repos folder once and the console tells, per repo, whether
// `<folder>/<org>/<repo>` exists, so the Open button offers Open or Clone, not both. Only
// folder names are looked up; no file is read. The handle is kept in this browser
// (IndexedDB), per login, and kept across sign-out like the rest of Profile. Read permission
// lapses on reload; `askFolderOnce` asks again from a click.

import { signal } from '@preact/signals';

/** Where the handles are kept: IndexedDB in the page, a map in tests. */
export interface HandleStore {
  get(login: string): Promise<FileSystemDirectoryHandle | null>;
  set(login: string, handle: FileSystemDirectoryHandle): Promise<void>;
  delete(login: string): Promise<void>;
}

const DB = 'dsl-console';
const STORE = 'folders';

function request<T>(r: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}

function openDb(): Promise<IDBDatabase> {
  const idb = globalThis.indexedDB;
  if (!idb) return Promise.reject(new Error('no IndexedDB'));
  const r = idb.open(DB, 1);
  r.onupgradeneeded = () => r.result.createObjectStore(STORE);
  return request(r);
}

async function withStore<T>(mode: IDBTransactionMode, run: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await openDb();
  try {
    return await request(run(db.transaction(STORE, mode).objectStore(STORE)));
  } finally {
    db.close();
  }
}

export const idbStore: HandleStore = {
  get: async (login) => ((await withStore('readonly', (s) => s.get(login))) as FileSystemDirectoryHandle | undefined) ?? null,
  set: async (login, handle) => void (await withStore('readwrite', (s) => s.put(handle, login))),
  delete: async (login) => void (await withStore('readwrite', (s) => s.delete(login))),
};

let store: HandleStore = idbStore;
/** This page load's handle reads, one per login, so every Open button shares one storage read. */
const handles = new Map<string, Promise<FileSystemDirectoryHandle | null>>();
/** The logins asked for permission this page load: once is enough. */
const asked = new Set<string>();

/** Bumped when a folder is picked, forgotten or granted, so every Open button checks again. */
export const folderChanged = signal(0);

/** Use another store and forget this load's handles (tests). */
export function setHandleStore(s: HandleStore): void {
  store = s;
  handles.clear();
  asked.clear();
}

/** Whether this browser can check folders at all. */
export const canCheckFolders = () => typeof globalThis.window?.showDirectoryPicker === 'function';

/** `login`'s picked folder, or null. Storage that fails reads as none. */
export function folderHandle(login: string): Promise<FileSystemDirectoryHandle | null> {
  let h = handles.get(login);
  if (!h) {
    h = store.get(login).catch(() => null); // storage unavailable: no folder
    handles.set(login, h);
  }
  return h;
}

/** Ask the person for their repos folder and keep it; null when they cancel. */
export async function pickFolder(login: string): Promise<FileSystemDirectoryHandle | null> {
  let h: FileSystemDirectoryHandle;
  try {
    h = await window.showDirectoryPicker!({ id: 'dsl-repos', mode: 'read' });
  } catch {
    return null; // cancelled, or refused
  }
  handles.set(login, Promise.resolve(h));
  asked.add(login); // picking it granted read
  try {
    await store.set(login, h);
  } catch {
    /* storage unavailable: kept until reload */
  }
  folderChanged.value++;
  return h;
}

export async function forgetFolder(login: string): Promise<void> {
  handles.set(login, Promise.resolve(null));
  try {
    await store.delete(login);
  } catch {
    /* storage unavailable: nothing was kept */
  }
  folderChanged.value++;
}

/**
 * From a click: ask for read permission on `login`'s folder when the browser would prompt,
 * once per page load. Resolves when there is nothing to ask or the person answered.
 */
export async function askFolderOnce(login: string): Promise<void> {
  if (asked.has(login) || !canCheckFolders()) return;
  asked.add(login);
  const h = await folderHandle(login);
  if (!h?.queryPermission || !h.requestPermission) return;
  try {
    if ((await h.queryPermission({ mode: 'read' })) !== 'prompt') return;
    if ((await h.requestPermission({ mode: 'read' })) === 'granted') folderChanged.value++;
  } catch {
    /* no answer: both entries stay */
  }
}

/** No folder of that name: nothing there (NotFoundError), or a file (TypeMismatchError). */
const isNotThere = (e: unknown) => ['NotFoundError', 'TypeMismatchError'].includes((e as { name?: string } | null)?.name ?? '');

/**
 * Whether `<handle>/<org>/<repo>` is a folder. A handle already named for the org is the
 * course folder itself (as `courseFolder` treats a typed path). Undefined when the browser
 * does not grant read, or answers something else than "not there".
 */
export async function clonedIn(handle: FileSystemDirectoryHandle, org: string, repo: string): Promise<boolean | undefined> {
  try {
    if (handle.queryPermission && (await handle.queryPermission({ mode: 'read' })) !== 'granted') return undefined;
    const course = handle.name.toLowerCase() === org.toLowerCase() ? handle : await handle.getDirectoryHandle(org);
    await course.getDirectoryHandle(repo);
    return true;
  } catch (e) {
    return isNotThere(e) ? false : undefined;
  }
}

/** Whether `repo` of `org` is cloned in `login`'s picked folder; undefined when the console cannot tell. */
export async function isCloned(login: string, org: string, repo: string): Promise<boolean | undefined> {
  // Safari and Firefox: no check, and no IndexedDB database made for nothing.
  if (!canCheckFolders()) return undefined;
  const h = await folderHandle(login);
  return h ? clonedIn(h, org, repo) : undefined;
}

/** The last segment of a typed folder, for comparing it with the picked folder's name. */
export const lastSegment = (folder: string) => folder.trim().replace(/[\\/]+$/, '').split(/[\\/]/).pop() ?? '';
