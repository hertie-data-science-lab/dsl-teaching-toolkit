// @vitest-environment happy-dom
// The reading list in the course Materials file view and the Released materials viewer
// (decision 0044): a READINGS.md, or a path a readings entry names, is marked in the course
// tree and edited in place (the same editor as the schedule entry sheet, saved on its own).

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient, decodeBase64 } from '../src/github/client';
import type { Course } from '../src/model/discovery';
import type { Loaded } from '../src/model/status';
import type { Release, Status } from '../src/model/types';
import { MaterialsIndexScreen } from '../src/screens/CourseIndex';
import type { CourseProps } from '../src/screens/types';
import { LIST_TEXT, isReadingList, notCited } from '../src/ui/ReadingList';
import { FakeGitHub, fileBody, json, type Seen } from './fake';
import example from './fixtures/status.example.json';
import { StaticFiles } from './staticFiles';

const ORG = 'hertie-dsl-demo-course-e1234';
const COHORT = 'hertie-dsl-demo-f2026';
const MAT = 'course-materials-f2026';
const NAMED = 'lectures/05/week5-reading.md';
const PATHS = ['readings/04/READINGS.md', 'readings/04/bishop.pdf', 'readings/04/murphy.pdf', NAMED, 'lectures/05/notes.md'];
const blob = (path: string) => ({ path, mode: '100644', type: 'blob', sha: `sha-${path}`, size: 10 });
const course: Course = { org: ORG, name: 'Machine Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: [{ org: COHORT, term: 'f2026', termLabel: 'Fall 2026' }], meta: null };
const readings5: Release = {
  id: 'readings-5', when: '2026-10-13T09:00:00+02:00', kind: 'readings', number: 5, title: 'Readings 5', state: 'released', tbc: false, show_on_site: true,
  source: { repo: MAT, path: 'lectures/05' }, dest: { repo: 'materials', path: 'lectures/05' },
  reading_list: { source: { repo: MAT, path: NAMED }, dest: { repo: 'materials', path: NAMED } },
};
const ready: Loaded = { kind: 'ready', status: { ...(example as unknown as Status), releases: [readings5] }, sha: 's', stale: [] };
const TEXT: Record<string, string> = { 'readings/04/READINGS.md': '- Bishop (2006): bishop.pdf\n', [NAMED]: '- Week five.\n', 'lectures/05/notes.md': '# Notes' };
const staticFiles = () => new StaticFiles(Object.fromEntries(Object.entries(TEXT).map(([k, v]) => [`${ORG}/${MAT}/${k}`, v])), {}, { [`${ORG}/${MAT}`]: PATHS, [`${COHORT}/materials`]: [NAMED] });
const cp = (file: string, write = true): CourseProps => ({ course: { ...course, write }, loaded: { kind: 'absent' }, cohortStates: { [COHORT]: ready }, files: staticFiles(), now: Date.parse('2026-10-01T10:00:00Z'), file });

let root: HTMLElement | null = null;
let gh: FakeGitHub;
beforeEach(() => sessionStorage.clear());
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
});

async function settle() {
  for (let i = 0; i < 8; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
}
async function mount(view: preact.VNode) {
  gh = new FakeGitHub()
    .on('GET', `/repos/${ORG}/${MAT}/git/trees/HEAD?recursive=1`, { sha: 't', truncated: false, tree: PATHS.map(blob) })
    .on('GET', `/repos/${COHORT}/materials/git/trees/HEAD?recursive=1`, { sha: 't', truncated: false, tree: [blob(NAMED)] });
  for (const [path, text] of Object.entries(TEXT)) gh.on('GET', `/repos/${ORG}/${MAT}/contents/${path}`, fileBody(path, text));
  gh.on('GET', `/repos/${COHORT}/materials/contents/${NAMED}`, fileBody(NAMED, TEXT[NAMED]))
    .on('PUT', /\/contents\//, () => json({ content: { sha: 'b1' }, commit: { sha: 'c1' } }));
  const env = { user: { login: 'prof', id: 1, name: 'P', email: null, avatar_url: '' }, client: new GitHubClient({ token: () => 't', fetch: gh.fetch }), files: staticFiles() } as unknown as Env;
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(() => render(<EnvCtx.Provider value={env}>{view}</EnvCtx.Provider>, root!));
  await settle();
  return root;
}
const button = (text: string) => [...root!.querySelectorAll('button')].find((b) => b.textContent?.trim() === text);
const chipped = () => [...root!.querySelectorAll('.ft-file')].filter((li) => li.querySelector('.chip')?.textContent === 'reading list').map((li) => li.querySelector('.ft-name')!.textContent);

describe('which files are reading lists', () => {
  it('READINGS.md in any case, or a path a readings entry names', () => {
    expect(isReadingList('readings/04/READINGS.md')).toBe(true);
    expect(isReadingList('x/readings.md')).toBe(true);
    expect(isReadingList(NAMED)).toBe(false);
    expect(isReadingList(NAMED, [NAMED])).toBe(true);
    expect(notCited('- bishop.pdf', ['bishop.pdf', 'murphy.pdf'])).toEqual(['murphy.pdf']);
    expect(notCited('  ', ['bishop.pdf'])).toEqual([]);
  });
});

describe('the course Materials file view (decision 0044)', () => {
  it('marks READINGS.md and the entry-named file in the tree, and edits READINGS.md in place', async () => {
    await mount(<MaterialsIndexScreen {...cp(`${MAT}/readings/04/READINGS.md`)} />);
    expect(chipped().sort()).toEqual(['READINGS.md']); // the open folder only; lectures/05 stays closed
    const edit = button('Edit reading list')!;
    expect(edit).toBeDefined();
    await act(() => edit.click());
    await settle();
    // The same editor and words as the entry sheet; the path fixed, the folder's PDFs as reading files.
    expect(root!.textContent).toContain(LIST_TEXT.label);
    expect(root!.textContent).toContain(LIST_TEXT.hint);
    expect(root!.textContent).toContain('File: course-materials-f2026 / readings/04/READINGS.md');
    expect(root!.textContent).toContain(LIST_TEXT.existingFile);
    expect(root!.textContent).toContain(LIST_TEXT.footExisting);
    expect(root!.textContent).toContain(LIST_TEXT.missing(['murphy.pdf']));
    expect(button(LIST_TEXT.choose)).toBeUndefined();
    const area = root!.querySelector<HTMLTextAreaElement>('#mf-rl')!;
    expect(area.value).toBe(TEXT['readings/04/READINGS.md']);
    await act(() => { area.value = '- Bishop: bishop.pdf\n- Murphy: murphy.pdf\n'; area.dispatchEvent(new Event('input', { bubbles: true })); });
    expect(root!.textContent).not.toContain('not in the reading list');
    await act(() => button('Save')!.click());
    await settle();
    const put = gh.seen.filter((s: Seen) => s.method === 'PUT');
    expect(put.map((s) => s.url.split('/repos/')[1])).toEqual([`${ORG}/${MAT}/contents/readings/04/READINGS.md`]);
    const b = put[0].body as { content: string; sha: string; message: string };
    expect([decodeBase64(b.content), b.sha, b.message]).toEqual(['- Bishop: bishop.pdf\n- Murphy: murphy.pdf\n', 'static', 'readings: edit readings/04/READINGS.md, from the DSL Teaching Console']);
    await act(() => button('Back to the file')!.click());
    await settle();
    expect(button('Edit reading list')).toBeDefined();
  });

  it('offers Edit reading list on a file a semester’s readings entry names, not on other files', async () => {
    await mount(<MaterialsIndexScreen {...cp(`${MAT}/${NAMED}`)} />);
    expect(chipped()).toEqual(['week5-reading.md']);
    expect(button('Edit reading list')).toBeDefined();
    if (root) render(null, root);
    await mount(<MaterialsIndexScreen {...cp(`${MAT}/lectures/05/notes.md`)} />);
    expect(button('Edit reading list')).toBeUndefined();
  });

  it('offers no editor to someone who cannot change the course', async () => {
    await mount(<MaterialsIndexScreen {...cp(`${MAT}/readings/04/READINGS.md`, false)} />);
    expect(button('Edit reading list')).toBeUndefined();
  });
});
