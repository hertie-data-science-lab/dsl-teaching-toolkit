// @vitest-environment happy-dom
// The Open split button and Your setup (decisions 0017, 0024): the setup kept per login,
// every choice's link with and without a folder (Windows included), the remembered default,
// the menu from the keyboard and its `?`s, and Profile's view, edit and Save.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import { courseFolder, defaultItem, folderExample, mainLabel, openItems, orgFolder, platformOf, repoCloneCommand, schemeOk, withOverride, type RepoRef, type Setup } from '../src/model/open';
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
const hrefs = (setup: Setup | null, ref = REF) => Object.fromEntries(openItems(ref, setup).map((i) => [i.choice, i.href]));

describe('Your setup in this browser', () => {
  it('round-trips per login, keeps the folder when a choice is remembered, and is kept across sign-out', () => {
    const store = memStore();
    expect(yourSetup(LOGIN, store)).toBeNull();
    saveYourSetup(LOGIN, { folder: '/Users/a/repos', editor: 'other', scheme: 'zed://file/{path}' }, store);
    expect(yourSetup(LOGIN, store)).toEqual({ folder: '/Users/a/repos', editor: 'other', scheme: 'zed://file/{path}' });
    expect(yourSetup('someone-else', store)).toBeNull();
    expect(rememberOpen(LOGIN, 'githubdev', store)).toEqual({ folder: '/Users/a/repos', editor: 'other', scheme: 'zed://file/{path}', lastOpen: 'githubdev' });
    expect(rememberOpen('b-example', 'vsclone', store)).toEqual({ folder: '', editor: 'vscode', lastOpen: 'vsclone' });
    store.setItem(`dsl-console-visit:${LOGIN}:${ORG}`, '1');
    store.setItem(`dsl-console-paths:${LOGIN}`, '{}');
    forgetStudentPrefs(LOGIN, store);
    // Visit times and the old student folders go; Profile stays (decision 0021 rule 3).
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
    });
    expect(repoCloneCommand(REF, null)).toBe(`git clone ${GH}.git`);
    expect(openItems(REF, null).find((i) => i.choice === 'vscode')!.label).toBe('Clone in VS Code');
    // Another editor opens a folder or nothing: no folder, no entry.
    expect(hrefs({ folder: '', editor: 'other', scheme: 'zed://file/{path}' }).editor).toBeUndefined();
  });

  it('with a folder: the course gets its own folder inside it, and VS Code opens the clone', () => {
    const setup: Setup = { folder: '/Users/a b/repos/', editor: 'vscode' };
    const h = hrefs(setup);
    expect(h.vscode).toBe(`vscode://file/Users/a%20b/repos/${ORG}/assignment-2-f2026`);
    expect(repoCloneCommand(REF, setup)).toBe(`git clone ${GH}.git "/Users/a b/repos/${ORG}/assignment-2-f2026"`);
    expect(openItems(REF, setup).find((i) => i.choice === 'vscode')!.label).toBe('Open in VS Code');
    // A folder already named for the course org is not nested again.
    expect(orgFolder(`/Users/a/${ORG}`, ORG)).toBe(`/Users/a/${ORG}`);
    expect(orgFolder('', ORG)).toBe('');
    expect(courseFolder(null, ORG)).toBe('');
  });

  it('joins a Windows folder with backslashes and keeps the drive letter in the editor links', () => {
    const setup: Setup = { folder: 'C:\\Users\\a\\repos', editor: 'other', scheme: 'zed://file/{path}' };
    const h = hrefs(setup, { ...REF, path: 'notebooks/lab.ipynb' });
    expect(repoCloneCommand(REF, setup)).toBe(`git clone ${GH}.git "C:\\Users\\a\\repos\\${ORG}\\assignment-2-f2026"`);
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
    expect(pick({ folder: '/r', editor: 'vscode', lastOpen: 'vsclone' })).toBe('vsclone');
    // A last choice that is no longer offered (the editor changed) falls back.
    expect(pick({ folder: '', editor: 'vscode', lastOpen: 'editor' })).toBe('github');
  });

  it('with a folder offers both Open and Clone in VS Code; the folder check keeps the one that applies', () => {
    const setup: Setup = { folder: '/r', editor: 'other', scheme: 'zed://file/{path}' };
    const menu = (c?: boolean) => openItems(REF, setup, c).filter((i) => i.group === 'local').map((i) => i.label);
    // Clone entries before Open entries; the clone command is in Clone's ?, not the menu.
    expect(menu()).toEqual(['Clone in VS Code', 'Open in VS Code', 'Open in GitHub Desktop', 'Open in your editor']);
    expect(hrefs(setup).vsclone).toBe(`vscode://vscode.git/clone?url=${encodeURIComponent(GH)}`);
    expect(menu(true)).toEqual(['Open in VS Code', 'Open in GitHub Desktop', 'Open in your editor']);
    expect(menu(false)).toEqual(['Clone in VS Code', 'Open in GitHub Desktop']);
    // Without a folder the check changes nothing.
    expect(openItems(REF, null, true)).toEqual(openItems(REF, null));
  });

  it('keeps the remembered choice while offered, else takes its opposite', () => {
    const pick = (s: Setup, c?: boolean) => defaultItem(openItems(REF, s, c), s, c).choice;
    const vs: Setup = { folder: '/r', editor: 'vscode' };
    expect(pick({ ...vs, lastOpen: 'vsclone' })).toBe('vsclone');
    expect(pick({ ...vs, lastOpen: 'vscode' }, false)).toBe('vsclone');
    expect(pick({ ...vs, lastOpen: 'vsclone' }, true)).toBe('vscode');
    // A VS Code clone becomes a VS Code open, whatever the editor setting.
    expect(pick({ folder: '/r', editor: 'desktop', lastOpen: 'vsclone' }, true)).toBe('vscode');
    expect(pick({ folder: '/r', editor: 'other', scheme: 'zed://file/{path}' }, true)).toBe('editor');
    expect(pick({ folder: '/r', editor: 'other', scheme: 'zed://file/{path}', lastOpen: 'editor' }, false)).toBe('vsclone');
    expect(pick({ ...vs, lastOpen: 'desktop' }, false)).toBe('desktop');
    expect(pick(vs, false)).toBe('vsclone');
    expect(pick(vs, true)).toBe('vscode');
  });

  it('gives a course its own folder when Profile names one, and drops one that is empty or the default', () => {
    const base: Setup = { folder: '/Users/a/repos', editor: 'vscode' };
    const own = withOverride(base, ORG.toUpperCase(), '/Users/a/teaching/ml/');
    expect(own.overrides).toEqual({ [ORG]: '/Users/a/teaching/ml' });
    expect(courseFolder(own, ORG)).toBe('/Users/a/teaching/ml');
    expect(courseFolder(own, 'another-org')).toBe('/Users/a/repos/another-org');
    expect(hrefs(own).vscode).toBe('vscode://file/Users/a/teaching/ml/assignment-2-f2026');
    expect(repoCloneCommand(REF, own)).toBe(`git clone ${GH}.git "/Users/a/teaching/ml/assignment-2-f2026"`);
    expect(withOverride(own, ORG, '')).toEqual(base);
    expect(withOverride(own, ORG, `/Users/a/repos/${ORG}`)).toEqual(base);
    // A course folder alone, with no root, is enough to open there.
    expect(courseFolder(withOverride({ folder: '', editor: 'vscode' }, ORG, '/x'), ORG)).toBe('/x');
    // A student's fork goes in its semester's folder, not one named for the login.
    expect(hrefs(base, { org: LOGIN, repo: 'materials', home: ORG }).vscode).toBe(`vscode://file/Users/a/repos/${ORG}/materials`);
    const store = memStore();
    saveYourSetup(LOGIN, own, store);
    expect(yourSetup(LOGIN, store)).toEqual(own);
    store.setItem(`dsl-console-setup:${LOGIN}`, JSON.stringify({ ...base, overrides: { [ORG]: 3, b: ' ' } }));
    expect(yourSetup(LOGIN, store)).toEqual(base);
  });

  it('labels the button Clone, Open, or Open or clone by what the folder check tells', () => {
    const at = (s: Setup | null, c?: boolean) => {
      const main = defaultItem(openItems(REF, s, c), s, c);
      return [main.choice, mainLabel(main, c)];
    };
    const vs: Setup = { folder: '/r', editor: 'vscode' };
    expect(at(vs, false)).toEqual(['vsclone', 'Clone']);
    expect(at(vs, true)).toEqual(['vscode', 'Open']);
    expect(at(vs)).toEqual(['vscode', 'Open or clone']);
    // A remembered web choice stays, with its own label, whatever the state.
    for (const c of [false, true, undefined]) expect(at({ ...vs, lastOpen: 'githubdev' }, c)).toEqual(['githubdev', 'Open on github.dev']);
    expect(at({ ...vs, lastOpen: 'github' }, false)).toEqual(['github', 'Open on GitHub']);
    expect(at({ folder: '/r', editor: 'desktop' }, false)).toEqual(['desktop', 'Clone']);
    expect(at({ folder: '/r', editor: 'other', scheme: 'zed://file/{path}' }, true)).toEqual(['editor', 'Open']);
    expect(at(null)).toEqual(['github', 'Open on GitHub']);
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
    const desktop = items().find((i) => i.textContent === 'Open in GitHub Desktop')!;
    expect(document.activeElement).toBe(desktop);
    expect(items().some((i) => i.textContent === 'Copy the clone command')).toBe(false);
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
    // Without the folder check the button cannot tell which step applies.
    expect(main.textContent).toBe('Open or clone');
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
    await act(() => void rows[0].querySelector('.hint-wrap')!.dispatchEvent(new MouseEvent('mouseenter')));
    expect(rows[0].querySelector<HTMLElement>('.hint-pop')!.hidden).toBe(false);
  });

  it('without a folder, the Clone ? names no course folder', async () => {
    await mount(<OpenButton {...REF} />);
    const rows = [...root!.querySelectorAll<HTMLElement>('.open-menu .pm-row')];
    expect(rows).toHaveLength(1);
    expect(rows[0].querySelector('.hint-pop')!.textContent).toBe(`VS Code asks where to put it. Or run: git clone ${GH}.git`);
  });

  it('links a student’s Profile by semester', async () => {
    await mount(<><OpenButton org={ORG} repo="materials" small /><OpenButton org={LOGIN} repo="materials" home="hertie-nlp-f2026" small /></>);
    const [a, b] = [...root!.querySelectorAll<HTMLElement>('.split')];
    const last = (el: Element) => [...el.querySelectorAll('[role="menuitem"]')].at(-1)!.getAttribute('href');
    expect(last(a)).toBe(`?course=${ORG}#profile`);
    expect(last(b)).toBe('?cohort=hertie-nlp-f2026#profile');
  });

  it('drops a remembered choice the menu no longer offers', async () => {
    localStorage.setItem(`dsl-console-setup:${LOGIN}`, JSON.stringify({ folder: '', editor: 'vscode', lastOpen: 'clone' }));
    expect(yourSetup(LOGIN)).toEqual({ folder: '', editor: 'vscode' });
    await mount(<OpenButton {...REF} />);
    expect(q('.split-main').textContent).toBe('Open on GitHub');
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

  it('lists the courses with their folders, and stores a course’s own folder', async () => {
    saveYourSetup(LOGIN, { folder: '/Users/a/repos', editor: 'vscode' });
    const courses = [{ org: ORG, name: 'Machine Learning' }, { org: 'hertie-nlp-f2026', name: 'Natural Language Processing' }];
    await mount(<SetupScreen org={ORG} courses={courses} />);
    const rows = () => [...root!.querySelectorAll<HTMLElement>('.course-folders li')];
    expect(root!.querySelector('.course-folders h2')!.textContent).toBe('Your courses');
    expect(rows().map((r) => r.querySelector('code')!.textContent)).toEqual([`/Users/a/repos/${ORG}`, '/Users/a/repos/hertie-nlp-f2026']);
    await act(() => button('Use a different folder').click());
    const input = q<HTMLInputElement>(`#ys-course-${ORG}`);
    expect(input.value).toBe(`/Users/a/repos/${ORG}`);
    await type(input, '/Users/a/teaching/ml');
    await act(() => button('Save').click());
    expect(yourSetup(LOGIN)?.overrides).toEqual({ [ORG]: '/Users/a/teaching/ml' });
    expect(rows()[0].querySelector('code')!.textContent).toBe('/Users/a/teaching/ml');
    // Editing the root keeps the course's own folder; the other course follows the root. A
    // row cannot take its own folder meanwhile: it would be saved against the draft root.
    await act(() => button('Edit').click());
    expect(button('Use a different folder')).toBeUndefined();
    expect(rows().map((r) => r.querySelector('code')!.textContent)).toEqual(['/Users/a/teaching/ml', '/Users/a/repos/hertie-nlp-f2026']);
    await type(q<HTMLInputElement>('#ys-folder'), '/Users/a/code');
    expect(rows()[1].querySelector('code')!.textContent).toBe('/Users/a/code/hertie-nlp-f2026');
    await act(() => button('Save').click());
    expect(yourSetup(LOGIN)).toEqual({ folder: '/Users/a/code', editor: 'vscode', overrides: { [ORG]: '/Users/a/teaching/ml' } });
  });

  it('says where the folder check works, in a browser without it', async () => {
    await mount(<SetupScreen org={ORG} />);
    expect(root!.textContent).toContain('In Chrome or Edge the console can see which repos you have cloned and offer only the step that applies.');
    expect(root!.querySelector('.course-folders')).toBeNull();
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
