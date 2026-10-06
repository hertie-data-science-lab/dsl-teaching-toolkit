// @vitest-environment happy-dom
// The folder check (decision 0023): a picked folder tells whether `<org>/<repo>` is there,
// a folder named for the org is the course folder itself, no permission means "cannot tell",
// permission is asked once per load from a click, and the Open button narrows its menu.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import { askFolderOnce, clonedIn, courseSteps, folderHandle, forgetFolder, isCloned, setHandleStore, type HandleStore } from '../src/model/localFolder';
import { lastSegment, type Setup } from '../src/model/open';
import { saveYourSetup } from '../src/model/prefs';
import { SetupScreen } from '../src/screens/Setup';
import { OpenButton } from '../src/ui/OpenButton';

const LOGIN = 'a-example';
const ORG = 'hertie-dsl-demo-course-e1234';

type Tree = { [name: string]: Tree | 'file' };
const notFound = () => new DOMException('not there', 'NotFoundError');

/** A fake directory handle over `tree`; `perm` is what queryPermission answers until requestPermission grants. */
function fakeDir(name: string, tree: Tree, perm: { state: PermissionState; requests: number } = { state: 'granted', requests: 0 }): FileSystemDirectoryHandle {
  return {
    kind: 'directory',
    name,
    queryPermission: async () => perm.state,
    requestPermission: async () => {
      perm.requests++;
      perm.state = 'granted';
      return perm.state;
    },
    getDirectoryHandle: async (n: string) => {
      const sub = tree[n];
      if (sub === undefined) throw notFound();
      if (sub === 'file') throw new DOMException('a file', 'TypeMismatchError');
      return fakeDir(n, sub, perm);
    },
  } as unknown as FileSystemDirectoryHandle;
}

function memHandles(init: Record<string, FileSystemDirectoryHandle> = {}): HandleStore & { data: Map<string, FileSystemDirectoryHandle> } {
  const data = new Map(Object.entries(init));
  return { data, get: async (l) => data.get(l) ?? null, set: async (l, h) => void data.set(l, h), delete: async (l) => void data.delete(l) };
}

const TREE: Tree = { [ORG]: { materials: {}, 'notes.txt': 'file' } };

describe('whether a repo is cloned', () => {
  it('finds <folder>/<org>/<repo>, and says no when a level is missing or is a file', async () => {
    const h = fakeDir('repositories', TREE);
    expect(await clonedIn(h, ORG, 'materials')).toBe(true);
    expect(await clonedIn(h, ORG, 'assignment-1-template')).toBe(false);
    expect(await clonedIn(h, ORG, 'notes.txt')).toBe(false);
    expect(await clonedIn(h, 'other-org', 'materials')).toBe(false);
  });

  it('takes a folder named for the org as the course folder itself', async () => {
    expect(await clonedIn(fakeDir(ORG.toUpperCase(), { materials: {} }), ORG, 'materials')).toBe(true);
  });

  it('looks in a course’s own folder when it sits inside the picked one, and cannot tell when it does not', async () => {
    const setup = (own: string): Setup => ({ folder: '/Users/a/repositories', editor: 'vscode', overrides: { [ORG]: own } });
    expect(courseSteps('repositories', setup('/Users/a/repositories/teaching/ml'), ORG)).toEqual(['teaching', 'ml']);
    expect(courseSteps('ml', setup('/Users/a/repositories/teaching/ml'), ORG)).toEqual([]);
    expect(courseSteps('repositories', setup('/elsewhere/ml'), ORG)).toBeNull();
    expect(courseSteps('repositories', null, ORG)).toEqual([ORG]);
    const h = fakeDir('repositories', { teaching: { ml: { materials: {} } } });
    expect(await clonedIn(h, ORG, 'materials', setup('/Users/a/repositories/teaching/ml'))).toBe(true);
    expect(await clonedIn(h, ORG, 'assignment-1-template', setup('/Users/a/repositories/teaching/ml'))).toBe(false);
    expect(await clonedIn(h, ORG, 'materials', setup('/elsewhere/ml'))).toBeUndefined();
  });

  it('cannot tell without read permission, or on another error', async () => {
    expect(await clonedIn(fakeDir('r', TREE, { state: 'prompt', requests: 0 }), ORG, 'materials')).toBeUndefined();
    const broken = { name: 'r', queryPermission: async () => 'granted', getDirectoryHandle: async () => { throw new DOMException('gone', 'NotAllowedError'); } } as unknown as FileSystemDirectoryHandle;
    expect(await clonedIn(broken, ORG, 'materials')).toBeUndefined();
  });

  it('cannot tell where the browser has no folder picker, and makes no storage read', async () => {
    const reads: string[] = [];
    setHandleStore({ get: async (l) => (reads.push(l), fakeDir('r', TREE)), set: async () => {}, delete: async () => {} });
    delete (window as { showDirectoryPicker?: unknown }).showDirectoryPicker;
    expect(await isCloned(LOGIN, ORG, 'materials')).toBeUndefined();
    expect(reads).toEqual([]);
  });

  it('reads the handle per login, and cannot tell with none or with broken storage', async () => {
    setHandleStore(memHandles({ [LOGIN]: fakeDir('repositories', TREE) }));
    expect(await isCloned(LOGIN, ORG, 'materials')).toBe(true);
    expect(await isCloned('someone-else', ORG, 'materials')).toBeUndefined();
    await forgetFolder(LOGIN);
    expect(await isCloned(LOGIN, ORG, 'materials')).toBeUndefined();
    setHandleStore({ get: async () => { throw new Error('blocked'); }, set: async () => {}, delete: async () => {} });
    expect(await folderHandle(LOGIN)).toBeNull();
  });

  it('asks for permission once per page load, and only when the browser would prompt', async () => {
    const perm = { state: 'prompt' as PermissionState, requests: 0 };
    setHandleStore(memHandles({ [LOGIN]: fakeDir('repositories', TREE, perm) }));
    expect(await isCloned(LOGIN, ORG, 'materials')).toBeUndefined();
    await askFolderOnce(LOGIN);
    expect(perm.requests).toBe(1);
    expect(await isCloned(LOGIN, ORG, 'materials')).toBe(true);
    perm.state = 'prompt';
    await askFolderOnce(LOGIN);
    expect(perm.requests).toBe(1);
  });

  it('compares the typed path by its last segment', () => {
    expect(lastSegment('/Users/a/repositories/')).toBe('repositories');
    expect(lastSegment('C:\\Users\\a\\repos')).toBe('repos');
    expect(lastSegment('')).toBe('');
  });
});

// --------------------------------------------------------------------------- in the page

const env = { user: { login: LOGIN, id: 1, name: 'A', email: null, avatar_url: '' } } as unknown as Env;
let root: HTMLElement | null = null;
beforeEach(() => {
  localStorage.clear();
  (window as { showDirectoryPicker?: unknown }).showDirectoryPicker = async () => fakeDir('repositories', TREE);
});
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
  delete (window as { showDirectoryPicker?: unknown }).showDirectoryPicker;
});
async function mount(ui: preact.VNode) {
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(() => render(<EnvCtx.Provider value={env}>{ui}</EnvCtx.Provider>, root!));
}
const labels = () => [...root!.querySelectorAll<HTMLElement>('[role="menuitem"]')].map((i) => i.textContent ?? '');
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });

describe('the Open button with a folder check', () => {
  it('shows Open for a cloned repo and Clone for one that is not', async () => {
    saveYourSetup(LOGIN, { folder: '/Users/a/repositories', editor: 'vscode' });
    setHandleStore(memHandles({ [LOGIN]: fakeDir('repositories', TREE) }));
    await mount(<><OpenButton org={ORG} repo="materials" /><OpenButton org={ORG} repo="assignment-1-template" /></>);
    await settle();
    const [a, b] = [...root!.querySelectorAll('.split')];
    const local = (el: Element) => [...el.querySelectorAll('[role="menuitem"]')].map((i) => i.textContent ?? '');
    expect(local(a)).toContain('Open in VS Code');
    expect(local(a)).not.toContain('Clone in VS Code');
    expect(local(b)).toContain('Clone in VS Code');
    expect(local(b)).not.toContain('Open in VS Code');
    expect(a.querySelector('.split-main')!.textContent).toBe('Open');
    expect(a.querySelector('.split-main')!.getAttribute('href')).toBe(`vscode://file/Users/a/repositories/${ORG}/materials`);
    expect(b.querySelector('.split-main')!.textContent).toBe('Clone');
    expect(b.querySelector('.split-main')!.getAttribute('href')).toMatch(/^vscode:\/\/vscode\.git\/clone/);
  });

  it('keeps both entries until permission is granted from the arrow', async () => {
    saveYourSetup(LOGIN, { folder: '/Users/a/repositories', editor: 'vscode' });
    const perm = { state: 'prompt' as PermissionState, requests: 0 };
    setHandleStore(memHandles({ [LOGIN]: fakeDir('repositories', TREE, perm) }));
    await mount(<OpenButton org={ORG} repo="assignment-1-template" />);
    await settle();
    expect(labels()).toEqual(expect.arrayContaining(['Open in VS Code', 'Clone in VS Code']));
    await act(() => root!.querySelector<HTMLButtonElement>('.split-caret')!.click());
    await settle();
    await settle();
    expect(perm.requests).toBe(1);
    expect(labels()).not.toContain('Open in VS Code');
  });
});

describe('Profile', () => {
  it('picks the folder, says which it checks, flags a name that differs, and forgets it', async () => {
    const store = memHandles();
    setHandleStore(store);
    saveYourSetup(LOGIN, { folder: '/Users/a/repos', editor: 'vscode' });
    await mount(<SetupScreen org={ORG} />);
    await settle();
    const pick = [...root!.querySelectorAll('button')].find((b) => b.textContent === 'Let the console see which repos are cloned')!;
    expect(root!.textContent).toContain('it reads nothing.');
    await act(() => pick.click());
    await settle();
    expect(store.data.get(LOGIN)?.name).toBe('repositories');
    expect(root!.textContent).toContain('Checking: repositories');
    expect(root!.textContent).toContain('The folder you picked is repositories; the path above ends in repos.');
    await act(() => [...root!.querySelectorAll('button')].find((b) => b.textContent === 'Forget')!.click());
    await settle();
    expect(store.data.has(LOGIN)).toBe(false);
    expect(root!.textContent).toContain('Let the console see which repos are cloned');
  });

  it('does not flag the course folder picked under the typed one, nor a difference in case', async () => {
    setHandleStore(memHandles({ [LOGIN]: fakeDir(ORG, { materials: {} }) }));
    saveYourSetup(LOGIN, { folder: '/Users/a/repos', editor: 'vscode' });
    await mount(<SetupScreen org={ORG} />);
    await settle();
    expect(root!.textContent).toContain(`Checking: ${ORG}`);
    expect(root!.textContent).not.toContain('The folder you picked');
    render(null, root!);
    setHandleStore(memHandles({ [LOGIN]: fakeDir('Repos', {}) }));
    await mount(<SetupScreen org={ORG} />);
    await settle();
    expect(root!.textContent).not.toContain('The folder you picked');
  });

  it('does not flag a course’s own folder picked, nor a semester’s folder for a student with no course', async () => {
    setHandleStore(memHandles({ [LOGIN]: fakeDir('ml', { materials: {} }) }));
    saveYourSetup(LOGIN, { folder: '/Users/a/repos', editor: 'vscode', overrides: { [ORG]: '/Users/a/teaching/ml' } });
    await mount(<SetupScreen org={ORG} courses={[{ org: ORG, name: 'Machine Learning' }]} />);
    await settle();
    expect(root!.textContent).toContain('Checking: ml');
    expect(root!.textContent).not.toContain('The folder you picked');
    render(null, root!);
    const SEM = 'hertie-nlp-f2026';
    setHandleStore(memHandles({ [LOGIN]: fakeDir(SEM, {}) }));
    saveYourSetup(LOGIN, { folder: '/Users/a/repos', editor: 'vscode' });
    await mount(<SetupScreen courses={[{ org: SEM, name: 'Natural Language Processing' }]} />);
    await settle();
    expect(root!.textContent).toContain(`Checking: ${SEM}`);
    expect(root!.textContent).not.toContain('The folder you picked');
    // Another name still is flagged.
    render(null, root!);
    setHandleStore(memHandles({ [LOGIN]: fakeDir('elsewhere', {}) }));
    await mount(<SetupScreen courses={[{ org: SEM, name: 'Natural Language Processing' }]} />);
    await settle();
    expect(root!.textContent).toContain('The folder you picked is elsewhere');
  });

  it('shows the stored handle in view mode, and keeps it across Edit and Cancel', async () => {
    setHandleStore(memHandles({ [LOGIN]: fakeDir('repos', {}) }));
    saveYourSetup(LOGIN, { folder: '/Users/a/repos', editor: 'vscode' });
    await mount(<SetupScreen org={ORG} />);
    await settle();
    const view = root!.querySelector('.setup-view')!;
    expect(view.textContent).toContain('Checking: repos');
    expect([...view.querySelectorAll('button')].some((b) => b.textContent === 'Forget')).toBe(true);
    const check = root!.querySelector('.folder-check');
    await act(() => root!.querySelector<HTMLButtonElement>('button[aria-label="Edit"]')!.click());
    // The same element: no remount, no flash of the pick button.
    expect(root!.querySelector('.folder-check')).toBe(check);
    expect(root!.textContent).toContain('Checking: repos');
    await act(() => [...root!.querySelectorAll('button')].find((b) => b.textContent === 'Cancel')!.click());
    expect(root!.querySelector('.folder-check')).toBe(check);
  });

  it('gives no Chrome or Edge footnote where the folder check works', async () => {
    setHandleStore(memHandles());
    await mount(<SetupScreen org={ORG} />);
    expect(root!.textContent).not.toContain('In Chrome or Edge');
  });

  it('keeps the folder check in view mode, and offers no clone block', async () => {
    setHandleStore(memHandles());
    saveYourSetup(LOGIN, { folder: '/Users/a/repos', editor: 'vscode' });
    await mount(<SetupScreen org={ORG} />);
    expect(root!.querySelector('.setup-view')).not.toBeNull();
    expect(root!.textContent).toContain('Let the console see which repos are cloned');
    expect(root!.textContent).not.toContain('Clone every repo');
    expect(root!.querySelector('.clone-all')).toBeNull();
  });
});
