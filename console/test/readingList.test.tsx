// @vitest-environment happy-dom
// The reading list in the course Materials file view and the Released materials viewer
// (decision 0044): a READINGS.md, or a path a readings entry names, is marked in the course
// tree and edited in place (the same editor as the schedule entry sheet, saved on its own);
// on the semester side it only links to its schedule entry.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient, decodeBase64 } from '../src/github/client';
import type { Course } from '../src/model/discovery';
import type { Loaded } from '../src/model/status';
import type { Release, Status } from '../src/model/types';
import { MaterialsIndexScreen } from '../src/screens/CourseIndex';
import { SemesterMaterialsScreen } from '../src/screens/SemesterMaterials';
import type { CourseProps } from '../src/screens/types';
import { LIST_TEXT, notCited } from '../src/ui/ReadingList';
import { isReadingList } from '../src/model/scheduleEdit';
import { DRAFT_TEXT } from '../src/ui/ReadingDraft';
import { GhMd, forgetRendered } from '../src/ui/rendered';
import { FakeGitHub, fileBody, json, type Seen } from './fake';
import example from './fixtures/status.example.json';
import { StaticFiles } from './staticFiles';

const ORG = 'hertie-dsl-demo-course-e1234';
const COHORT = 'hertie-dsl-demo-f2026';
const MAT = 'course-materials-f2026';
const NAMED = 'lectures/05/week5-reading.md';
// Where the engine says it lands in the semester (`released_path`): not the course path.
const RELEASED = 'week5/week5-reading.md';
const PATHS = ['readings/04/READINGS.md', 'readings/04/bishop.pdf', 'readings/04/murphy.pdf', NAMED, 'lectures/05/notes.md'];
const blob = (path: string) => ({ path, mode: '100644', type: 'blob', sha: `sha-${path}`, size: 10 });
const course: Course = { org: ORG, name: 'Machine Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: [{ org: COHORT, term: 'f2026', termLabel: 'Fall 2026' }], meta: null };
const readings5: Release = {
  id: 'readings-5', when: '2026-10-13T09:00:00+02:00', kind: 'readings', number: 5, title: 'Readings 5', state: 'released', tbc: false, show_on_site: true,
  source: { repo: MAT, path: 'lectures/05' }, dest: { repo: 'materials', path: 'lectures/05' },
  reading_list: { repo: MAT, path: NAMED, released_repo: 'materials', released_path: RELEASED },
};
const ready: Loaded = { kind: 'ready', status: { ...(example as unknown as Status), releases: [readings5] }, sha: 's', stale: [] };
const TEXT: Record<string, string> = { 'readings/04/READINGS.md': '- Bishop (2006): bishop.pdf\n', [NAMED]: '- Week five.\n', 'lectures/05/notes.md': '# Notes' };
const staticFiles = () => new StaticFiles(Object.fromEntries(Object.entries(TEXT).map(([k, v]) => [`${ORG}/${MAT}/${k}`, v])), {}, { [`${ORG}/${MAT}`]: PATHS, [`${COHORT}/materials`]: [RELEASED] });
const cp = (file: string, write = true): CourseProps => ({ course: { ...course, write }, loaded: { kind: 'absent' }, cohortStates: { [COHORT]: ready }, files: staticFiles(), now: Date.parse('2026-10-01T10:00:00Z'), file });

let root: HTMLElement | null = null;
let gh: FakeGitHub;
beforeEach(() => {
  sessionStorage.clear();
  forgetRendered(); // each test counts its own markdown renders
});
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
    .on('GET', `/repos/${COHORT}/materials/git/trees/HEAD?recursive=1`, { sha: 't', truncated: false, tree: [blob(RELEASED)] });
  for (const [path, text] of Object.entries(TEXT)) gh.on('GET', `/repos/${ORG}/${MAT}/contents/${path}`, fileBody(path, text));
  gh.on('GET', `/repos/${COHORT}/materials/contents/${RELEASED}`, fileBody(RELEASED, TEXT[NAMED]))
    .on('PUT', /\/contents\//, () => json({ content: { sha: 'b1' }, commit: { sha: 'c1' } }))
    .on('POST', '/markdown', (req: Seen) => new Response(`<p>${(req.body as { text: string }).text}</p>`, { status: 200 }));
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
  it('edits READINGS.md in place; the index tree beside it marks nothing (the chips are the repo page’s)', async () => {
    const p = cp(`${MAT}/readings/04/READINGS.md`);
    const refreshTree = vi.spyOn(p.files, 'refreshTree');
    await mount(<MaterialsIndexScreen {...p} />);
    expect(chipped()).toEqual([]);
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
    // Generate draft reads the folder's PDFs (decision 0044 point 6).
    expect(button(DRAFT_TEXT.button)!.disabled).toBe(false);
    expect(root!.textContent).toContain(DRAFT_TEXT.hint);
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
    // An existing file is already in the tree: only its cache entry changes (the save's own put).
    expect(refreshTree).not.toHaveBeenCalled();
    await act(() => button('Back to the file')!.click());
    await settle();
    expect(button('Edit reading list')).toBeDefined();
  });

  it('previews through GitHub’s markdown API with the file’s repo as context, once per settled text', async () => {
    await mount(<MaterialsIndexScreen {...cp(`${MAT}/readings/04/READINGS.md`)} />);
    const renders = () => gh.seen.filter((s: Seen) => s.method === 'POST' && s.url.endsWith('/markdown')).map((s) => s.body as { text: string; context?: string });
    // The file viewer's own rendering of the file, then the editor's preview: the same call, the same context.
    const viewer = renders();
    expect(viewer).toEqual([{ text: TEXT['readings/04/READINGS.md'], mode: 'gfm', context: `${ORG}/${MAT}` }]);
    await act(() => button('Edit reading list')!.click());
    await settle();
    expect(renders().slice(viewer.length)).toEqual(viewer);
    const area = root!.querySelector<HTMLTextAreaElement>('#mf-rl')!;
    const typeText = (v: string) => act(() => { area.value = v; area.dispatchEvent(new Event('input', { bubbles: true })); });
    const n = renders().length;
    // Typed and put back within the pause: the settled text never changed, so nothing is asked.
    await typeText('- Bishop (2006): bishop.pdf\nx');
    await typeText(TEXT['readings/04/READINGS.md']);
    await act(() => new Promise((r) => setTimeout(r, 500)));
    await settle();
    expect(renders().length).toBe(n);
    await typeText('- New.\n');
    await act(() => new Promise((r) => setTimeout(r, 500)));
    await settle();
    expect(renders().slice(n).map((b) => b.text)).toEqual(['- New.\n']);
  });

  it('offers Edit reading list on a file a semester’s readings entry names, not on other files', async () => {
    await mount(<MaterialsIndexScreen {...cp(`${MAT}/${NAMED}`)} />);
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

describe('the released reading list', () => {
  it('links to its schedule entry, with no editor', async () => {
    await mount(<SemesterMaterialsScreen course={course} cohort={course.cohorts[0]} loaded={ready} files={staticFiles()} now={Date.parse('2026-10-14T10:00:00Z')} file={`materials/${RELEASED}`} />);
    const link = [...root!.querySelectorAll('a')].find((a) => a.textContent === 'Edit this in the schedule entry')!;
    expect(link.getAttribute('href')).toBe(`?cohort=${COHORT}#schedule-readings-5`);
    expect(button('Edit reading list')).toBeUndefined();
    expect(root!.querySelector('textarea')).toBeNull();
  });
});

describe('the kept markdown renderings', () => {
  it('keeps the 20 most recently shown: an older one is asked for again', async () => {
    const asked: string[] = [];
    const env = { client: { renderMarkdown: async (text: string) => { asked.push(text); return `<p>${text}</p>`; } } } as unknown as Env;
    root = document.createElement('div');
    document.body.appendChild(root);
    const show = async (src: string) => { await act(() => render(<EnvCtx.Provider value={env}><GhMd src={src} /></EnvCtx.Provider>, root!)); await settle(); };
    for (let i = 0; i < 21; i++) await show(`t${i}`);
    await show('t20');
    await show('t1');
    expect(asked.length).toBe(21);
    await show('t0');
    expect(asked.length).toBe(22);
  });
});
