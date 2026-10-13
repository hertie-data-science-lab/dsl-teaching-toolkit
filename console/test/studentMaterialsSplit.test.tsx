// @vitest-environment happy-dom
// All materials with a file open (decision 0035 rule 11): the tree of every repo stays on
// screen beside the file, the open file's row marked and its folders open; each tree row is
// a file link with its menu (rule 10). A kind tab opens its files in the same split, its
// own list beside the file.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, expect, it } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient } from '../src/github/client';
import { factsFromStatus } from '../src/model/student';
import { KindView } from '../src/screens/StudentKind';
import { MaterialsView } from '../src/screens/StudentMaterials';
import { FakeGitHub, fileBody } from './fake';
import STATUS from './fixtures/student-status.json?raw';

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

async function mount(entry?: string, view?: preact.VNode) {
  const gh = new FakeGitHub()
    .on('GET', `/repos/${ORG}/materials/git/trees/HEAD?recursive=1`, tree(['labs/01/lab.md', 'labs/02/lab.md', 'SYLLABUS.md']))
    .on('GET', `/repos/${ORG}/notes/git/trees/HEAD?recursive=1`, tree(['week1/notes.pdf']))
    .on('GET', `/repos/${ORG}/materials/contents/labs/01/lab.md`, fileBody('labs/01/lab.md', '# Lab 1'))
    .on('POST', '/markdown', () => new Response('<h1>Lab 1</h1>', { status: 200 }));
  const env = { user: { login: LOGIN, id: 1, name: 'O', email: null, avatar_url: '' }, client: new GitHubClient({ token: () => 't', fetch: gh.fetch }) } as unknown as Env;
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(() => render(<EnvCtx.Provider value={env}>{view ?? <MaterialsView org={ORG} repos={['materials', 'notes']} entry={entry} />}</EnvCtx.Provider>, root!));
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

it('gives each tree row the menu, the same four items, a pdf’s Edit greyed', async () => {
  const el = await mount();
  expect(el.querySelector('.materials-split')).toBeNull();
  const row = (name: string) => [...el.querySelectorAll('.ft-file')].find((li) => li.querySelector('.ft-name')!.textContent === name)!;
  const items = (li: Element) => [...li.querySelectorAll('.file-menu-list > a, .file-menu-list > .fm-off')];
  const words = (li: Element) => items(li).map((a) => a.querySelector('.fm-lbl')!.textContent);
  const live = (li: Element) => items(li).filter((a) => !a.classList.contains('fm-off')).map((a) => a.querySelector('.fm-lbl')!.textContent);
  expect(words(row('SYLLABUS.md'))).toEqual(['view', 'GitHub', 'in browser', 'VS Code']);
  expect(live(row('SYLLABUS.md'))).toEqual(['view', 'GitHub', 'in browser']);
  expect(items(row('SYLLABUS.md'))[1].getAttribute('href')).toBe(`https://github.com/${ORG}/materials/blob/HEAD/SYLLABUS.md`);
  expect(words(row('notes.pdf'))).toEqual(['view', 'GitHub', 'in browser', 'VS Code']);
  expect(live(row('notes.pdf'))).toEqual(['view', 'GitHub']);
  expect(row('notes.pdf').querySelector('.ft-name')!.getAttribute('href')).toContain('#materials-');
});

it('opens a kind tab’s file in that tab, the tab’s list beside it and the file marked', async () => {
  const doc = JSON.parse(STATUS);
  doc.semester = ORG;
  doc.materials_repos = ['materials'];
  const lec = doc.rows.find((r: { id: string }) => r.id === 'lecture_01');
  lec.links = [{ name: 'Lab 1', repo: 'materials', path: 'labs/01/lab.md', url: `https://github.com/${ORG}/materials/blob/main/labs/01/lab.md` }];
  const f = factsFromStatus(doc);
  const el = await mount(undefined, <KindView facts={f} kind="lecture" org={ORG} now={Date.parse('2026-10-08T12:00:00Z')} entry="materials/labs/01/lab.md" />);
  const split = el.querySelector('.materials-split')!;
  const nav = split.querySelector('.materials-tree')!;
  expect(nav.getAttribute('aria-label')).toBe('Lectures');
  expect(nav.querySelector('.kind-tab')).not.toBeNull();
  const marked = [...nav.querySelectorAll('a[aria-current]')];
  expect(marked.map((a) => a.textContent)).toEqual(['Lab 1']);
  expect(marked[0].getAttribute('href')).toBe(`?semester=${ORG}#kind-lecture/${encodeURIComponent('materials/labs/01/lab.md')}`);
  expect(split.querySelector('.materials-file h2')!.textContent).toBe('labs/01/lab.md');
  expect(split.querySelector('.materials-file .md-body')!.innerHTML).toBe('<h1>Lab 1</h1>');
  // A repo that is not a materials repo: the list alone.
  if (root) render(null, root);
  const plain = await mount(undefined, <KindView facts={f} kind="lecture" org={ORG} now={Date.parse('2026-10-08T12:00:00Z')} entry="elsewhere/x.md" />);
  expect(plain.querySelector('.materials-split')).toBeNull();
  expect(plain.querySelector('.kind-tab')).not.toBeNull();
});
