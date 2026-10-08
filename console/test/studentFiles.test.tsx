// @vitest-environment happy-dom
// A released file's button row (decision 0035 rule 10): the name, then source, online (github.dev
// in the student's fork once the fork check found one, else the org's) and local (the editor
// from Profile, in the semester's folder); online and local only for a file an editor opens,
// local only with a folder and an editor that takes a path; one fork read per repo per session,
// and none for a row that needs none.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient } from '../src/github/client';
import { editableFile, fileLocal, fileOnline, type Setup } from '../src/model/open';
import { saveYourSetup } from '../src/model/prefs';
import type { FileLink } from '../src/model/student';
import { FileChips, FileList } from '../src/screens/StudentFiles';
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
});

describe('the button row', () => {
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
  const btns = (li: Element) => Object.fromEntries([...li.querySelectorAll<HTMLAnchorElement>('.file-btns a')].map((a) => [a.textContent, a]));

  it('edits online in the student’s fork, once the check finds it, and reads it once per repo', async () => {
    const gh = new FakeGitHub().on('GET', `/repos/${LOGIN}/materials`, { name: 'materials', fork: true, parent: { full_name: `${ORG}/materials` }, html_url: '' });
    const el = await mount(<FileList org={ORG} repos={['materials']} links={[link('labs/lab.ipynb'), link('notes.md'), link('slides.pdf')]} />, gh);
    const [nb, md, pdf] = [...el.querySelectorAll('li')].map(btns);
    expect(nb.source.getAttribute('href')).toBe(`https://github.com/${ORG}/materials/blob/main/labs/lab.ipynb`);
    expect(nb.online.getAttribute('href')).toBe(`https://github.dev/${LOGIN}/materials/blob/main/labs/lab.ipynb`);
    expect(nb.online.title).toBe('Edit lab.ipynb in your fork, in the browser');
    expect(md.online.getAttribute('target')).toBe('_blank');
    expect(Object.keys(pdf)).toEqual(['source']);
    // No folder in Profile: no local.
    expect(nb.local).toBeUndefined();
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
    expect(b.online.getAttribute('href')).toBe(`https://github.dev/${ORG}/materials/blob/main/labs/lab.ipynb`);
    expect(b.online.title).toBe('Edit lab.ipynb in the browser');
    expect(b.local.getAttribute('href')).toBe(`zed://file/Users/o/repos/${ORG}/materials/labs/lab.ipynb`);
    expect(b.local.title).toBe('Open lab.ipynb in your editor');
    // A custom scheme opens no new tab.
    expect(b.local.hasAttribute('target')).toBe(false);
  });

  it('links source to the GitHub blob when the link carries no url', async () => {
    const el = await mount(<FileList org={ORG} repos={['materials']} links={[{ ...link('slides.pdf'), url: '' }]} />, new FakeGitHub());
    expect(btns(el).source.getAttribute('href')).toBe(`https://github.com/${ORG}/materials/blob/HEAD/slides.pdf`);
  });

  it('reads no fork for rows that need none, and gives a link outside the org no buttons', async () => {
    const gh = new FakeGitHub();
    const outside: FileLink = { name: 'paper', url: 'https://example.org/paper.html' };
    const el = await mount(<FileList org={ORG} repos={['materials']} links={[link('slides.pdf'), outside]} />, gh);
    expect(forkReads(gh)).toBe(0);
    expect(el.querySelectorAll('.file-btns')).toHaveLength(1);
    expect(el.querySelector('a[href="https://example.org/paper.html"]')!.getAttribute('target')).toBe('_blank');
  });
});
