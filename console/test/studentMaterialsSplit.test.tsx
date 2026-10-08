// @vitest-environment happy-dom
// All materials with a file open (decision 0035 rule 11): the tree of every repo stays on
// screen beside the file, the open file's row marked and its folders open; each tree row is
// a file link with its button row (rule 10).

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, expect, it } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient } from '../src/github/client';
import { MaterialsView } from '../src/screens/StudentMaterials';
import { FakeGitHub, fileBody } from './fake';

const ORG = 'hertie-dsl-demo-f2026';
const LOGIN = 'octo-student';
const blob = (path: string) => ({ path, mode: '100644', type: 'blob', sha: `sha-${path}`, size: 10 });
const tree = (paths: string[]) => ({ sha: 't', truncated: false, tree: paths.map(blob) });

let root: HTMLElement | null = null;
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
});

async function mount(entry?: string) {
  const gh = new FakeGitHub()
    .on('GET', `/repos/${ORG}/materials/git/trees/HEAD?recursive=1`, tree(['labs/01/lab.md', 'labs/02/lab.md', 'SYLLABUS.md']))
    .on('GET', `/repos/${ORG}/notes/git/trees/HEAD?recursive=1`, tree(['week1/notes.pdf']))
    .on('GET', `/repos/${ORG}/materials/contents/labs/01/lab.md`, fileBody('labs/01/lab.md', '# Lab 1'))
    .on('POST', '/markdown', () => new Response('<h1>Lab 1</h1>', { status: 200 }));
  const env = { user: { login: LOGIN, id: 1, name: 'O', email: null, avatar_url: '' }, client: new GitHubClient({ token: () => 't', fetch: gh.fetch }) } as unknown as Env;
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(() => render(<EnvCtx.Provider value={env}><MaterialsView org={ORG} repos={['materials', 'notes']} entry={entry} /></EnvCtx.Provider>, root!));
  for (let i = 0; i < 6; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return root;
}

it('keeps every repo’s tree beside the open file, the file marked and its folders open', async () => {
  const el = await mount('materials/labs/01/lab.md');
  const split = el.querySelector('.materials-split')!;
  expect(split).not.toBeNull();
  const nav = split.querySelector('.materials-tree')!;
  expect([...nav.querySelectorAll('h2')].map((h) => h.textContent)).toEqual(['materials', 'notes']);
  const marked = [...nav.querySelectorAll('a[aria-current]')];
  expect(marked.map((a) => a.textContent)).toEqual(['lab.md']);
  expect(marked[0].closest('li')!.classList.contains('current')).toBe(true);
  expect(marked[0].closest('li')!.parentElement!.closest('details')!.open).toBe(true);
  // The sibling folder stays folded.
  const two = [...nav.querySelectorAll('.ft-dir')].find((li) => li.textContent!.startsWith('02/'))!;
  expect(two.querySelector('details')!.open).toBe(false);
  // The file shows beside the tree, not instead of it.
  expect(split.querySelector('.materials-file h2')!.textContent).toBe('labs/01/lab.md');
});

it('gives each tree row the button row, a pdf source only', async () => {
  const el = await mount();
  expect(el.querySelector('.materials-split')).toBeNull();
  const row = (name: string) => [...el.querySelectorAll('.ft-file')].find((li) => li.querySelector('.ft-name')!.textContent === name)!;
  const words = (li: Element) => [...li.querySelectorAll('.file-btns a')].map((a) => a.textContent);
  expect(words(row('SYLLABUS.md'))).toEqual(['source', 'online']);
  expect(row('SYLLABUS.md').querySelector('.file-btns a')!.getAttribute('href')).toBe(`https://github.com/${ORG}/materials/blob/HEAD/SYLLABUS.md`);
  expect(words(row('notes.pdf'))).toEqual(['source']);
  expect(row('notes.pdf').querySelector('.ft-name')!.getAttribute('href')).toContain('#materials-');
});
