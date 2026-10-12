// @vitest-environment happy-dom
// A released file's menu (decision 0035 rule 10): the name, then one chevron whose menu has
// Open - view (the console's viewer in a new tab, for a materials file it draws; GitHub's blob
// page for any other file in the org GitHub draws) and GitHub - and Edit - in browser (github.dev
// in the student's fork once the fork check found one, else the org's) and the editor from
// Profile by name, in the semester's folder; Edit only for a file an editor opens, the editor
// only with a folder and an editor that takes a path, a pointer to Profile until then; one fork
// read per repo per session, and none for a row that needs none.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient } from '../src/github/client';
import { editableFile, editorName, fileLocal, fileOnline, type Setup } from '../src/model/open';
import { saveYourSetup } from '../src/model/prefs';
import type { FileLink } from '../src/model/student';
import { FileChips, FileLinkItem, FileList } from '../src/screens/StudentFiles';
import { FakeGitHub } from './fake';

const LOGIN = 'octo-student';
const ORG = 'hertie-nlp-f2026';
const link = (path: string, repo = 'materials'): FileLink => ({ name: path.split('/').pop()!, repo, path, url: `https://github.com/${ORG}/${repo}/blob/main/${path}` });

describe('the URLs', () => {
  it('leaves documents and archives to source only', () => {
    expect(['lab.ipynb', 'notes.md', 'run.py', 'Makefile', 'data.csv'].every(editableFile)).toBe(true);
    expect(['slides.pdf', 'deck.PPTX', 'a.docx', 'b.xlsx', 'all.zip'].some(editableFile)).toBe(false);
  });

  it('opens a file on github.dev in the given owner’s copy, on the branch its GitHub link names', () => {
    expect(fileOnline(LOGIN, 'materials', 'labs/01 intro/lab.ipynb')).toBe(`https://github.dev/${LOGIN}/materials/blob/HEAD/labs/01%20intro/lab.ipynb`);
    expect(fileOnline(LOGIN, 'materials', 'a.md', `https://github.com/${ORG}/materials/blob/spring-2026/a.md`)).toBe(`https://github.dev/${LOGIN}/materials/blob/spring-2026/a.md`);
    expect(fileOnline(ORG, 'materials', 'a.md', 'https://example.org/a.md')).toBe(`https://github.dev/${ORG}/materials/blob/HEAD/a.md`);
  });

  it('opens a file in the editor, in the semester’s folder, only with a folder and an editor that takes a path', () => {
    const vs: Setup = { folder: '/Users/a b/repos', editor: 'vscode' };
    expect(fileLocal(vs, ORG, 'materials', 'labs/lab.ipynb')).toBe(`vscode://file/Users/a%20b/repos/${ORG}/materials/labs/lab.ipynb`);
    expect(fileLocal({ ...vs, overrides: { [ORG]: '/elsewhere/nlp' } }, ORG, 'materials', 'a.md')).toBe('vscode://file/elsewhere/nlp/materials/a.md');
    expect(fileLocal({ folder: 'C:\\Users\\a\\repos', editor: 'other', scheme: 'zed://file/{path}' }, ORG, 'materials', 'a.md')).toBe(`zed://file/C:/Users/a/repos/${ORG}/materials/a.md`);
    expect(fileLocal({ ...vs, editor: 'desktop' }, ORG, 'materials', 'a.md')).toBeNull();
    expect(fileLocal({ ...vs, editor: 'other', scheme: 'zed' }, ORG, 'materials', 'a.md')).toBeNull();
    expect(fileLocal({ folder: '', editor: 'vscode' }, ORG, 'materials', 'a.md')).toBeNull();
    expect(fileLocal(null, ORG, 'materials', 'a.md')).toBeNull();
  });

  it('names the editor Profile names, by its scheme for another editor, else nothing', () => {
    expect(editorName({ folder: '', editor: 'vscode' })).toBe('VS Code');
    expect(editorName({ folder: '', editor: 'other', scheme: 'cursor://file/{path}' })).toBe('Cursor');
    expect(editorName({ folder: '', editor: 'other', scheme: 'myeditor://open/{path}' })).toBe('');
    expect(editorName({ folder: '', editor: 'desktop' })).toBe('');
    expect(editorName(null)).toBe('');
  });
});

describe('the file menu', () => {
  let root: HTMLElement | null = null;
  beforeEach(() => localStorage.clear());
  afterEach(() => {
    if (root) render(null, root);
    root?.remove();
    root = null;
  });

  async function mount(v: preact.VNode, gh: FakeGitHub) {
    const env = { user: { login: LOGIN, id: 1, name: 'O', email: null, avatar_url: '' }, client: new GitHubClient({ token: () => 't', fetch: gh.fetch }) } as unknown as Env;
    root = document.createElement('div');
    document.body.appendChild(root);
    await act(() => render(<EnvCtx.Provider value={env}>{v}</EnvCtx.Provider>, root!));
    for (let i = 0; i < 4; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    return root;
  }
  const forkReads = (gh: FakeGitHub) => gh.seen.filter((x) => x.url.includes(`/repos/${LOGIN}/`)).length;
  const btns = (li: Element) => Object.fromEntries([...li.querySelectorAll<HTMLAnchorElement>('.file-menu [role="menuitem"]')].map((a) => [a.querySelector('.fm-lbl')!.textContent, a]));
  const desc = (a: Element) => a.querySelector('small')?.textContent;
  const SET_UP = 'Set up your editor in Profile';

  it('edits online in the student’s fork, once the check finds it, and reads it once per repo', async () => {
    const gh = new FakeGitHub().on('GET', `/repos/${LOGIN}/materials`, { name: 'materials', fork: true, parent: { full_name: `${ORG}/materials` }, html_url: '' });
    const el = await mount(<FileList org={ORG} repos={['materials']} links={[link('labs/lab.ipynb'), link('notes.md'), link('slides.pdf')]} />, gh);
    const [nb, md, pdf] = [...el.querySelectorAll('li')].map(btns);
    expect(nb.GitHub.getAttribute('href')).toBe(`https://github.com/${ORG}/materials/blob/main/labs/lab.ipynb`);
    expect(nb['in browser'].getAttribute('href')).toBe(`https://github.dev/${LOGIN}/materials/blob/main/labs/lab.ipynb`);
    expect(desc(nb['in browser'])).toBe('VS Code in your browser, in your fork');
    expect(md['in browser'].getAttribute('target')).toBe('_blank');
    expect(Object.keys(pdf)).toEqual(['view', 'GitHub']);
    // No folder in Profile: a pointer to Profile in the editor's place, in the console.
    expect(Object.keys(nb)).toEqual(['view', 'GitHub', 'in browser', SET_UP]);
    expect(nb[SET_UP].getAttribute('href')).toBe(`?cohort=${ORG}#profile`);
    expect(nb[SET_UP].hasAttribute('target')).toBe(false);
    expect(forkReads(gh)).toBe(1);
    // The name still opens the file in the console.
    expect(el.querySelector('li > .file-link > a')!.getAttribute('href')).toContain('#materials-');
  });

  it('edits online in the org’s repo without a fork, and opens locally through the editor Profile names', async () => {
    saveYourSetup(LOGIN, { folder: '/Users/o/repos', editor: 'other', scheme: 'zed://file/{path}' });
    const gh = new FakeGitHub();
    const el = await mount(<FileChips org={ORG} repos={['materials']} links={[link('labs/lab.ipynb')]} />, gh);
    const b = btns(el);
    expect(el.querySelector('a.st-chip')!.textContent).toBe('lab.ipynb');
    expect(b['in browser'].getAttribute('href')).toBe(`https://github.dev/${ORG}/materials/blob/main/labs/lab.ipynb`);
    expect(desc(b['in browser'])).toBe('VS Code in your browser');
    expect(b.Zed.getAttribute('href')).toBe(`zed://file/Users/o/repos/${ORG}/materials/labs/lab.ipynb`);
    expect(desc(b.Zed)).toBe(`In your local copy: /Users/o/repos/${ORG}/materials`);
    // A custom scheme opens no new tab.
    expect(b.Zed.hasAttribute('target')).toBe(false);
    if (root) render(null, root);
    // An editor with no name it is known by is `local`.
    saveYourSetup(LOGIN, { folder: '/Users/o/repos', editor: 'other', scheme: 'myeditor://open/{path}' });
    expect(Object.keys(btns(await mount(<FileChips org={ORG} repos={['materials']} links={[link('labs/lab.ipynb')]} />, gh)))).toEqual(['view', 'GitHub', 'in browser', 'local']);
  });

  it('renders a file the viewer draws in a new tab: in All materials, or in the kind tab it is listed in', async () => {
    const files = ['slides.pdf', 'notes.md', 'labs/lab.ipynb', 'deck.html', 'fig.png', 'code.py', 'essay.docx'].map((p) => link(p));
    const el = await mount(<FileList org={ORG} repos={['materials']} links={[...files, link('slides.pdf', 'handouts')]} />, new FakeGitHub());
    const rows = [...el.querySelectorAll('li')].map(btns);
    expect(rows.map((b) => Object.keys(b))).toEqual([
      ['view', 'GitHub'], ['view', 'GitHub', 'in browser', SET_UP], ['view', 'GitHub', 'in browser', SET_UP], ['view', 'GitHub', 'in browser', SET_UP], ['view', 'GitHub', 'in browser', SET_UP],
      ['GitHub', 'in browser', SET_UP], ['GitHub'],
      // Not in a materials repo: the console cannot open it, so view is GitHub's blob page.
      ['view', 'GitHub'],
    ]);
    expect(rows[7].view.getAttribute('href')).toBe(`https://github.com/${ORG}/handouts/blob/main/slides.pdf`);
    expect(desc(rows[7].view)).toBe('Rendered, on GitHub');
    const pdf = rows[0].view;
    expect(pdf.getAttribute('href')).toBe(`?semester=${ORG}#materials-${encodeURIComponent('materials/slides.pdf')}`);
    expect(pdf.getAttribute('target')).toBe('_blank');
    expect(pdf.getAttribute('rel')).toBe('noopener');
    expect(desc(pdf)).toBe('Rendered, here');
    if (root) render(null, root);
    const tab = await mount(<FileList org={ORG} repos={['materials']} links={[link('slides.pdf')]} tab="kind-lecture" current="materials/slides.pdf" />, new FakeGitHub());
    const name = tab.querySelector('li > .file-link > a')!;
    expect(name.getAttribute('href')).toBe(`?semester=${ORG}#kind-lecture/${encodeURIComponent('materials/slides.pdf')}`);
    expect(name.getAttribute('aria-current')).toBe('page');
    expect(btns(tab).view.getAttribute('href')).toBe(name.getAttribute('href'));
  });

  it('renders a file outside the materials repos on GitHub only when GitHub draws it', async () => {
    const files = ['brief.md', 'lab.ipynb', 'fig.png', 'slides.pdf', 'page.html', 'run.py', 'a.docx'].map((p) => link(p, 'assignment-1-octo'));
    const el = await mount(<FileList org={ORG} repos={['materials']} links={files} />, new FakeGitHub());
    const rows = [...el.querySelectorAll('li')].map(btns);
    expect(rows.map((b) => 'view' in b)).toEqual([true, true, true, true, false, false, false]);
    const blob = (p: string) => `https://github.com/${ORG}/assignment-1-octo/blob/main/${p}`;
    expect(rows[0].view.getAttribute('href')).toBe(blob('brief.md'));
    // Markdown and notebooks: GitHub shows the text, so it differs from view; the rest keep the plain blob.
    expect(rows.map((b) => b.GitHub.getAttribute('href'))).toEqual([
      `${blob('brief.md')}?plain=1`, `${blob('lab.ipynb')}?plain=1`, blob('fig.png'), blob('slides.pdf'), blob('page.html'), blob('run.py'), blob('a.docx'),
    ]);
    expect(rows[0].view.getAttribute('target')).toBe('_blank');
  });

  it('draws the name alone in brackets for a sentence, and a label in place of the name', async () => {
    const el = await mount(<FileLinkItem org={ORG} repos={['materials']} link={link('slides.pdf')} setup={null} inline />, new FakeGitHub());
    expect(el.textContent).toBe('[slides.pdf]');
    expect(el.querySelector('.file-menu')).toBeNull();
    if (root) render(null, root);
    const btn = await mount(<FileLinkItem org={ORG} repos={['materials']} link={link('SYLLABUS.md')} setup={null} cls="btn outline" label={<b>Syllabus</b>} />, new FakeGitHub());
    expect(btn.querySelector('a.btn')!.innerHTML).toBe('<b>Syllabus</b>');
    expect(Object.keys(btns(btn))).toEqual(['view', 'GitHub', 'in browser', SET_UP]);
  });

  it('links GitHub to the blob when the link carries no url', async () => {
    const el = await mount(<FileList org={ORG} repos={['materials']} links={[{ ...link('slides.pdf'), url: '' }]} />, new FakeGitHub());
    expect(btns(el).GitHub.getAttribute('href')).toBe(`https://github.com/${ORG}/materials/blob/HEAD/slides.pdf`);
  });

  it('reads no fork for rows that need none, and gives a link outside the org no menu', async () => {
    const gh = new FakeGitHub();
    const outside: FileLink = { name: 'paper', url: 'https://example.org/paper.html' };
    const el = await mount(<FileList org={ORG} repos={['materials']} links={[link('slides.pdf'), outside]} />, gh);
    expect(forkReads(gh)).toBe(0);
    expect(el.querySelectorAll('.file-menu')).toHaveLength(1);
    expect(el.querySelector('a[href="https://example.org/paper.html"]')!.getAttribute('target')).toBe('_blank');
  });

  it('opens from the chevron, one at a time, and closes on Escape or a click outside', async () => {
    const el = await mount(<FileList org={ORG} repos={['materials']} links={[link('notes.md'), link('slides.pdf')]} />, new FakeGitHub());
    const [a, b] = [...el.querySelectorAll<HTMLButtonElement>('.file-chev')];
    const menuOf = (c: HTMLButtonElement) => c.parentElement!.querySelector<HTMLElement>('[role="menu"]')!;
    expect(a.title).toBe('More ways to open');
    expect(a.getAttribute('aria-label')).toBe('More ways to open notes.md');
    expect(menuOf(a).hidden).toBe(true);
    await act(() => a.click());
    expect(menuOf(a).hidden).toBe(false);
    expect(a.getAttribute('aria-expanded')).toBe('true');
    // Its first item takes focus, so the keys work at once.
    expect(document.activeElement).toBe(menuOf(a).querySelector('[role="menuitem"]'));
    await act(() => b.click());
    expect(menuOf(a).hidden).toBe(true);
    expect(menuOf(b).hidden).toBe(false);
    await act(() => { menuOf(b).dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); });
    expect(menuOf(b).hidden).toBe(true);
    expect(document.activeElement).toBe(b);
    await act(() => a.click());
    await act(() => document.body.click());
    expect(menuOf(a).hidden).toBe(true);
    // A pdf's menu has no Edit heading.
    expect([...menuOf(b).querySelectorAll('.pm-h')].map((h) => h.textContent)).toEqual(['Open']);
    expect([...menuOf(a).querySelectorAll('.pm-h')].map((h) => h.textContent)).toEqual(['Open', 'Edit']);
  });
});
