// @vitest-environment happy-dom
// The Open split button and Your setup (decisions 0017, 0024): the setup kept per login,
// every choice's link with and without a folder (Windows included), the remembered default,
// the menu from the keyboard and its `?`s, and Profile's view, edit and Save.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import { courseFolder, defaultItem, folderExample, openItems, platformOf, schemeOk, type RepoRef, type Setup } from '../src/model/open';
import { forgetStudentPrefs, rememberOpen, resetKeptSetups, saveYourSetup, yourSetup, type PrefStore } from '../src/model/prefs';
import { SetupScreen } from '../src/screens/Setup';
import { OpenButton } from '../src/ui/OpenButton';

const LOGIN = 'a-example';
const ORG = 'hertie-dsl-demo-course-e1234';
const REF: RepoRef = { org: ORG, repo: 'assignment-2-f2026' };
const GH = `https://github.com/${ORG}/assignment-2-f2026`;

function memStore(): PrefStore & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return { data, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v), removeItem: (k) => void data.delete(k), get length() { return data.size; }, key: (i) => [...data.keys()][i] ?? null };
}
const refusing: PrefStore = { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); } };
const hrefs = (setup: Setup | null, ref = REF) => Object.fromEntries(openItems(ref, setup).map((i) => [i.choice, i.href ?? i.copy]));

describe('Your setup in this browser', () => {
  it('round-trips per login, keeps the folder when a choice is remembered, and is kept across sign-out', () => {
    const store = memStore();
    expect(yourSetup(LOGIN, store)).toBeNull();
    saveYourSetup(LOGIN, { folder: '/Users/a/repos', editor: 'other', scheme: 'zed://file/{path}' }, store);
    expect(yourSetup(LOGIN, store)).toEqual({ folder: '/Users/a/repos', editor: 'other', scheme: 'zed://file/{path}' });
    expect(yourSetup('someone-else', store)).toBeNull();
    expect(rememberOpen(LOGIN, 'githubdev', store)).toEqual({ folder: '/Users/a/repos', editor: 'other', scheme: 'zed://file/{path}', lastOpen: 'githubdev' });
    expect(rememberOpen('b-example', 'clone', store)).toEqual({ folder: '', editor: 'vscode', lastOpen: 'clone' });
    store.setItem(`dsl-console-visit:${LOGIN}:${ORG}`, '1');
    store.setItem(`dsl-console-paths:${LOGIN}`, '{}');
    forgetStudentPrefs(LOGIN, store);
    // Visit times and student folders go; Profile stays (decision 0021 rule 3).
    expect([...store.data.keys()].sort()).toEqual([`dsl-console-setup:${LOGIN}`, 'dsl-console-setup:b-example']);
    expect(yourSetup(LOGIN, store)?.folder).toBe('/Users/a/repos');
  });

  it('reads a damaged entry as defaults and survives blocked storage', () => {
    const store = memStore();
    store.setItem(`dsl-console-setup:${LOGIN}`, JSON.stringify({ folder: 3, editor: 'emacs', lastOpen: 'ftp' }));
    expect(yourSetup(LOGIN, store)).toEqual({ folder: '', editor: 'vscode' });
    store.setItem(`dsl-console-setup:${LOGIN}`, '{not json');
    expect(yourSetup(LOGIN, store)).toBeNull();
    expect(yourSetup(LOGIN, refusing)).toBeNull();
    // A refused write is kept until reload, across sign-out too.
    expect(saveYourSetup(LOGIN, { folder: '/x', editor: 'vscode' }, refusing)).toBe(false);
    expect(yourSetup(LOGIN, refusing)).toEqual({ folder: '/x', editor: 'vscode' });
    forgetStudentPrefs(LOGIN, refusing);
    expect(yourSetup(LOGIN, refusing)).toEqual({ folder: '/x', editor: 'vscode' });
    resetKeptSetups();
    expect(yourSetup(LOGIN, refusing)).toBeNull();
  });
});

describe('where each choice opens', () => {
  it('without a folder: VS Code clones, Desktop opens the repo, the clone command names no folder', () => {
    expect(hrefs(null)).toEqual({
      github: GH,
      githubdev: `https://github.dev/${ORG}/assignment-2-f2026`,
      vscode: `vscode://vscode.git/clone?url=${encodeURIComponent(GH)}`,
      desktop: `x-github-client://openRepo/${GH}`,
      clone: `git clone ${GH}.git`,
    });
    expect(openItems(REF, null).find((i) => i.choice === 'vscode')!.label).toBe('Clone in VS Code');
    // Another editor opens a folder or nothing: no folder, no entry.
    expect(hrefs({ folder: '', editor: 'other', scheme: 'zed://file/{path}' }).editor).toBeUndefined();
  });

  it('with a folder: the course gets its own folder inside it, and VS Code opens the clone', () => {
    const setup: Setup = { folder: '/Users/a b/repos/', editor: 'vscode' };
    const h = hrefs(setup);
    expect(h.vscode).toBe(`vscode://file/Users/a%20b/repos/${ORG}/assignment-2-f2026`);
    expect(h.clone).toBe(`git clone ${GH}.git "/Users/a b/repos/${ORG}/assignment-2-f2026"`);
    expect(openItems(REF, setup).find((i) => i.choice === 'vscode')!.label).toBe('Open in VS Code');
    // A folder already named for the course org is not nested again.
    expect(courseFolder(`/Users/a/${ORG}`, ORG)).toBe(`/Users/a/${ORG}`);
    expect(courseFolder('', ORG)).toBe('');
  });

  it('joins a Windows folder with backslashes and keeps the drive letter in the editor links', () => {
    const setup: Setup = { folder: 'C:\\Users\\a\\repos', editor: 'other', scheme: 'zed://file/{path}' };
    const h = hrefs(setup, { ...REF, path: 'notebooks/lab.ipynb' });
    expect(h.clone).toBe(`git clone ${GH}.git "C:\\Users\\a\\repos\\${ORG}\\assignment-2-f2026"`);
    expect(h.vscode).toBe(`vscode://file/C:/Users/a/repos/${ORG}/assignment-2-f2026/notebooks/lab.ipynb`);
    expect(h.editor).toBe(`zed://file/C:/Users/a/repos/${ORG}/assignment-2-f2026/notebooks/lab.ipynb`);
    expect(h.github).toBe(`${GH}/tree/main/notebooks/lab.ipynb`);
  });

  it('carries a branch to GitHub, github.dev, the VS Code clone and Desktop', () => {
    const h = hrefs(null, { ...REF, branch: 'solution' });
    expect(h.github).toBe(`${GH}/tree/solution`);
    expect(h.githubdev).toBe(`https://github.dev/${ORG}/assignment-2-f2026/tree/solution`);
    expect(h.vscode).toBe(`vscode://vscode.git/clone?url=${encodeURIComponent(GH)}&ref=solution`);
    expect(h.desktop).toBe(`x-github-client://openRepo/${GH}?branch=solution`);
  });

  it('defaults to the last choice, else the editor once a folder is set up, else GitHub', () => {
    const pick = (s: Setup | null) => defaultItem(openItems(REF, s), s).choice;
    expect(pick(null)).toBe('github');
    expect(pick({ folder: '', editor: 'desktop' })).toBe('github');
    expect(pick({ folder: '/r', editor: 'desktop' })).toBe('desktop');
    expect(pick({ folder: '/r', editor: 'other', scheme: 'zed://file/{path}' })).toBe('editor');
    expect(pick({ folder: '/r', editor: 'vscode', lastOpen: 'clone' })).toBe('clone');
    // A last choice that is no longer offered (the editor changed) falls back.
    expect(pick({ folder: '', editor: 'vscode', lastOpen: 'editor' })).toBe('github');
  });

  it('with a folder offers both Open and Clone in VS Code; the folder check keeps the one that applies', () => {
    const setup: Setup = { folder: '/r', editor: 'other', scheme: 'zed://file/{path}' };
    const menu = (c?: boolean) => openItems(REF, setup, c).filter((i) => i.group === 'local').map((i) => i.label);
    // Clone entries before Open entries, the clone command last.
    expect(menu()).toEqual(['Clone in VS Code', 'Open in VS Code', 'Open in GitHub Desktop', 'Open in your editor', 'Copy the clone command']);
    expect(hrefs(setup).vsclone).toBe(`vscode://vscode.git/clone?url=${encodeURIComponent(GH)}`);
    expect(menu(true)).toEqual(['Open in VS Code', 'Open in GitHub Desktop', 'Open in your editor']);
    expect(menu(false)).toEqual(['Clone in VS Code', 'Open in GitHub Desktop', 'Copy the clone command']);
    // Without a folder the check changes nothing.
    expect(openItems(REF, null, true)).toEqual(openItems(REF, null));
  });

  it('keeps the remembered choice while offered, else takes its opposite', () => {
    const pick = (s: Setup, c?: boolean) => defaultItem(openItems(REF, s, c), s).choice;
    const vs: Setup = { folder: '/r', editor: 'vscode' };
    expect(pick({ ...vs, lastOpen: 'vsclone' })).toBe('vsclone');
    expect(pick({ ...vs, lastOpen: 'vscode' }, false)).toBe('vsclone');
    expect(pick({ ...vs, lastOpen: 'vsclone' }, true)).toBe('vscode');
    expect(pick({ ...vs, lastOpen: 'clone' }, true)).toBe('vscode');
    // A VS Code clone becomes a VS Code open, whatever the editor setting.
    expect(pick({ folder: '/r', editor: 'desktop', lastOpen: 'vsclone' }, true)).toBe('vscode');
    expect(pick({ folder: '/r', editor: 'other', scheme: 'zed://file/{path}', lastOpen: 'clone' }, true)).toBe('editor');
    expect(pick({ folder: '/r', editor: 'other', scheme: 'zed://file/{path}', lastOpen: 'editor' }, false)).toBe('vsclone');
    expect(pick({ ...vs, lastOpen: 'desktop' }, false)).toBe('desktop');
    expect(pick(vs, false)).toBe('vsclone');
    expect(pick(vs, true)).toBe('vscode');
  });

  it('checks an editor link and spells the example folder for the platform', () => {
    expect(schemeOk('zed://file/{path}')).toBe(true);
    expect(schemeOk('subl:{path}')).toBe(true);
    expect(schemeOk('zed://file')).toBe(false);
    expect(platformOf({ platform: 'Win32' })).toBe('win');
    expect(platformOf({ userAgentData: { platform: 'Linux' } })).toBe('linux');
    expect(platformOf({ platform: 'MacIntel' })).toBe('mac');
    expect(folderExample('win')).toBe('C:\\Users\\you\\Documents\\repositories');
    expect(folderExample('linux')).toBe('/home/you/repositories');
  });
});

// --------------------------------------------------------------------------- in the page

const env = { user: { login: LOGIN, id: 1, name: 'A', email: null, avatar_url: '' } } as unknown as Env;
let root: HTMLElement | null = null;
beforeEach(() => localStorage.clear());
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
});
async function mount(ui: preact.VNode) {
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(() => render(<EnvCtx.Provider value={env}>{ui}</EnvCtx.Provider>, root!));
}
const q = <T extends Element = HTMLElement>(sel: string) => root!.querySelector<T>(sel)!;
const key = (el: Element, k: string) => act(() => void el.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true })));
const items = () => [...root!.querySelectorAll<HTMLElement>('[role="menuitem"]')];

describe('the Open button', () => {
  it('opens its menu from the keyboard, moves through it, and gives focus back on Escape', async () => {
    await mount(<OpenButton {...REF} />);
    const caret = q<HTMLButtonElement>('.split-caret');
    expect(q('.split-main').textContent).toBe('Open on GitHub');
    expect(caret.getAttribute('aria-haspopup')).toBe('menu');
    expect(caret.getAttribute('aria-expanded')).toBe('false');
    await key(caret, 'ArrowDown');
    expect(caret.getAttribute('aria-expanded')).toBe('true');
    expect(document.activeElement).toBe(items()[0]);
    await key(items()[0], 'ArrowDown');
    expect(document.activeElement?.textContent).toBe('Open on github.dev');
    await key(items()[1], 'End');
    expect(document.activeElement?.textContent).toBe('Set up a local folder');
    await key(items()[0], 'ArrowUp');
    expect(document.activeElement?.textContent).toMatch(/^Copy the clone command/);
    const desktop = items().find((i) => i.textContent === 'Open in GitHub Desktop')!;
    await key(document.activeElement!, 'ArrowUp');
    expect(document.activeElement).toBe(desktop);
    desktop.addEventListener('click', (e) => e.preventDefault());
    await act(() => desktop.click());
    expect(q('[role="menu"]').hidden).toBe(true);
    expect(document.activeElement).toBe(caret);
    await key(caret, 'ArrowDown');
    expect(document.activeElement).toBe(items()[0]);
    expect(items().at(-1)!.getAttribute('href')).toBe(`?course=${ORG}#profile`);
    await key(items()[0], 'Escape');
    expect(q('[role="menu"]').hidden).toBe(true);
    expect(document.activeElement).toBe(caret);
  });

  it('remembers the choice made, so the button and the others on the page do it next', async () => {
    await mount(<><OpenButton {...REF} /><OpenButton org={ORG} repo="materials" small /></>);
    const dev = items().find((i) => i.textContent === 'Open on github.dev')!;
    dev.addEventListener('click', (e) => e.preventDefault());
    await act(() => dev.click());
    expect(yourSetup(LOGIN)?.lastOpen).toBe('githubdev');
    const mains = [...root!.querySelectorAll<HTMLAnchorElement>('.split-main')];
    expect(mains.map((m) => m.textContent)).toEqual(['Open on github.dev', 'Open']);
    expect(mains[1].getAttribute('aria-label')).toBe('Open on github.dev');
    expect(mains[1].getAttribute('href')).toBe(`https://github.dev/${ORG}/materials`);
    expect(mains[1].getAttribute('target')).toBe('_blank');
  });

  it('opens the local folder in the saved editor, in the same tab', async () => {
    saveYourSetup(LOGIN, { folder: '/Users/a/repos', editor: 'vscode' });
    await mount(<OpenButton {...REF} />);
    const main = q<HTMLAnchorElement>('.split-main');
    expect(main.textContent).toBe('Open in VS Code');
    expect(main.getAttribute('href')).toBe(`vscode://file/Users/a/repos/${ORG}/assignment-2-f2026`);
    expect(main.hasAttribute('target')).toBe(false);
    expect(items().at(-1)!.textContent).toBe('Change your profile');
  });

  it('puts a ? on Clone and Open in VS Code, the clone command in it, and keeps the ? off the arrow keys', async () => {
    saveYourSetup(LOGIN, { folder: '/Users/a/repos', editor: 'vscode' });
    await mount(<OpenButton {...REF} />);
    const rows = [...root!.querySelectorAll<HTMLElement>('.open-menu .pm-row')];
    expect(rows.map((r) => r.querySelector('[role="menuitem"]')!.textContent)).toEqual(['Clone in VS Code', 'Open in VS Code']);
    const [clone, open] = rows.map((r) => r.querySelector<HTMLButtonElement>('.hint-btn')!);
    expect(open.getAttribute('aria-label')).toBe('About opening in VS Code');
    expect(clone.getAttribute('aria-label')).toBe('About cloning in VS Code');
    expect(rows[1].textContent).toContain('Opens the repo’s folder on your computer. Clone it first if it is not there yet.');
    expect(rows[0].querySelector('.hint-pop')!.textContent).toBe(`VS Code asks where to put it; choose your course folder. Or run: git clone ${GH}.git "/Users/a/repos/${ORG}/assignment-2-f2026"`);
    // The command no longer sits under the menu row.
    expect(items().find((i) => i.textContent === 'Copy the clone command')!.querySelector('code')).toBeNull();
    // Each ? describes the item beside it.
    for (const r of rows) expect(r.querySelector('[role="menuitem"]')!.getAttribute('aria-describedby')).toBe(r.querySelector('.hint-pop')!.id);
    // Not an item: out of the tab order and skipped by the arrows.
    expect(clone.tabIndex).toBe(-1);
    expect(items().some((i) => i.classList.contains('hint-btn'))).toBe(false);
    await key(q('.split-caret'), 'ArrowDown');
    for (let i = 0; i < 3; i++) await key(document.activeElement!, 'ArrowDown');
    expect(document.activeElement?.textContent).toBe('Open in VS Code');
    await key(document.activeElement!, 'ArrowDown');
    expect(document.activeElement?.textContent).toBe('Open in GitHub Desktop');
    // Hover still opens it.
    await act(() => void rows[0].querySelector('.hint')!.dispatchEvent(new MouseEvent('mouseenter')));
    expect(rows[0].querySelector<HTMLElement>('.hint-pop')!.hidden).toBe(false);
  });

  it('without a folder, the Clone ? names no course folder', async () => {
    await mount(<OpenButton {...REF} />);
    const rows = [...root!.querySelectorAll<HTMLElement>('.open-menu .pm-row')];
    expect(rows).toHaveLength(1);
    expect(rows[0].querySelector('.hint-pop')!.textContent).toBe(`VS Code asks where to put it. Or run: git clone ${GH}.git`);
  });

  it('shows the clone command under its entry when copying fails, and keeps the button label', async () => {
    const clip = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: () => Promise.reject(new Error('denied')) } });
    try {
      rememberOpen(LOGIN, 'clone');
      await mount(<OpenButton {...REF} />);
      const main = q<HTMLButtonElement>('.split-main');
      expect(main.textContent).toBe('Copy the clone command');
      expect(root!.querySelector('.open-menu > .pm-cmd')).toBeNull();
      await act(async () => {
        main.click();
        await new Promise((r) => setTimeout(r, 0));
      });
      expect(q('[role="menu"]').hidden).toBe(false);
      expect(main.textContent).toBe('Copy the clone command');
      expect(q('.sr[role="status"]').textContent).toBe('Could not copy: select it below');
      const cmd = q('.open-menu > .pm-cmd');
      expect(cmd.textContent).toBe(`git clone ${GH}.git`);
      expect(cmd.getAttribute('role')).not.toBe('menuitem');
      expect(cmd.previousElementSibling!.textContent).toBe('Copy the clone command');
    } finally {
      if (clip) Object.defineProperty(navigator, 'clipboard', clip);
      else delete (navigator as { clipboard?: unknown }).clipboard;
    }
  });
});

const button = (text: string) => [...root!.querySelectorAll('button')].find((b) => (b.textContent || b.getAttribute('aria-label')) === text)!;
const type = (el: HTMLInputElement, value: string) =>
  act(() => {
    el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });

describe('Profile', () => {
  it('shows the form while nothing is saved, stores on Save, then shows what is saved as text', async () => {
    await mount(<SetupScreen org={ORG} />);
    expect(q('h1').textContent).toBe('Profile');
    expect(root!.querySelector('.lede')).toBeNull();
    expect(root!.textContent).toContain('Each course gets a folder inside it, named after its organisation.');
    expect(root!.textContent).toContain('Kept in this browser, for your GitHub login.');
    // No clone block, no File System Access API, no Cancel with nothing saved.
    expect(root!.textContent).not.toContain('Clone every repo');
    expect(root!.textContent).not.toContain('which repos are cloned');
    expect(button('Cancel')).toBeUndefined();
    await type(q<HTMLInputElement>('#ys-folder'), '/Users/a/repos');
    const radios = [...root!.querySelectorAll<HTMLInputElement>('input[type="radio"]')];
    await act(() => radios[2].click());
    const save = button('Save') as HTMLButtonElement;
    await type(q<HTMLInputElement>('#ys-scheme'), 'zed://file');
    expect(save.disabled).toBe(true);
    await type(q<HTMLInputElement>('#ys-scheme'), 'zed://file/{path}');
    await act(() => save.click());
    expect(yourSetup(LOGIN)).toEqual({ folder: '/Users/a/repos', editor: 'other', scheme: 'zed://file/{path}' });
    expect(root!.querySelector('#ys-folder')).toBeNull();
    expect([...root!.querySelectorAll('.setup-view dt')].map((d) => d.textContent)).toEqual(['Folder for your course repos', 'Editor']);
    expect([...root!.querySelectorAll('.setup-view dd')].map((d) => d.textContent)).toEqual(['/Users/a/repos', 'Another editor, zed://file/{path}']);
    expect(root!.querySelector('.setup-view dd code')!.textContent).toBe('/Users/a/repos');
    expect(root!.textContent).toContain('Saved.');
    expect(document.activeElement).toBe(button('Edit'));
  });

  it('switches to the form with the pencil, and Cancel discards the draft', async () => {
    saveYourSetup(LOGIN, { folder: '/Users/a/repos', editor: 'desktop' });
    await mount(<SetupScreen org={ORG} />);
    expect(root!.querySelector('#ys-folder')).toBeNull();
    expect(root!.querySelectorAll('.setup-view button[aria-label="Edit"]')).toHaveLength(1);
    expect([...root!.querySelectorAll('.setup-view dd')].map((d) => d.textContent)).toEqual(['/Users/a/repos', 'GitHub Desktop']);
    await act(() => button('Edit').click());
    const folder = q<HTMLInputElement>('#ys-folder');
    expect(folder.value).toBe('/Users/a/repos');
    expect(document.activeElement).toBe(folder);
    await type(folder, '/elsewhere');
    await act(() => button('Cancel').click());
    expect(root!.querySelector('#ys-folder')).toBeNull();
    expect(root!.querySelector('.setup-view dd')!.textContent).toBe('/Users/a/repos');
    expect(yourSetup(LOGIN)).toEqual({ folder: '/Users/a/repos', editor: 'desktop' });
    // Editing again starts from what is saved, not the discarded draft.
    await act(() => button('Edit').click());
    expect(q<HTMLInputElement>('#ys-folder').value).toBe('/Users/a/repos');
  });

  it('makes the editor the default again when the folder or editor changes', async () => {
    rememberOpen(LOGIN, 'github');
    await mount(<SetupScreen org={ORG} />);
    // The remembered choice alone names no folder: the form shows.
    await type(q<HTMLInputElement>('#ys-folder'), '/Users/a/repos');
    await act(() => button('Save').click());
    expect(yourSetup(LOGIN)).toEqual({ folder: '/Users/a/repos', editor: 'vscode' });
  });
});
