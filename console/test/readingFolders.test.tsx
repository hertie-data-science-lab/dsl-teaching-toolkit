// @vitest-environment happy-dom
// Decision 0046 rules 5-7: a readings folder with files and no READINGS.md is flagged on the
// course materials tree, suggested by the engine, and its list is written from the course side.

import { render } from 'preact';
import { renderToString } from 'preact-render-to-string';
import { act } from 'preact/test-utils';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient, decodeBase64 } from '../src/github/client';
import type { Course } from '../src/model/discovery';
import { foldersWithoutList, readingFolderState, readingListFolder, readingListHref } from '../src/model/readingFolders';
import { suggestionsCount } from '../src/model/readiness';
import type { CourseStatus, MaterialsCheck, Todo } from '../src/model/types';
import { parseSearch } from '../src/router';
import { MaterialsChecklist, courseItems, todoHref } from '../src/screens/Course';
import { MaterialsIndexScreen } from '../src/screens/CourseIndex';
import type { CourseProps } from '../src/screens/types';
import { MaterialsTree } from '../src/ui/MaterialsTree';
import { LIST_TEXT } from '../src/ui/ReadingList';
import { FakeGitHub, json, type Seen } from './fake';
import { StaticFiles } from './staticFiles';

const ORG = 'hertie-dsl-demo-course-e1234';
const MAT = 'course-materials-f2026';
// The same cases as the engine's `test_the_readings_folders_are_the_folders_directly_under_readings`.
const PATHS = [
  'readings/01_intro/READINGS.md', 'readings/01_intro/paper.pdf',
  'readings/02_trees/Breiman.pdf', 'readings/02_trees/.gitkeep', 'readings/02_trees/extra/deep.pdf',
  'readings/03_empty/.gitkeep', 'readings/04_nested/more/x.pdf', 'readings/.gitkeep', 'readings/loose.pdf',
  'lectures/01_intro/slides.pdf',
];
const files = (paths: string[]) => paths.map((path) => ({ path, dir: false }));
const WHY = 'The folder has reading files but no READINGS.md. Students see the files only; a list says what to read and how.';

describe('the readings folders (the engine’s rule)', () => {
  it('counts the files directly inside each folder under readings/, and finds READINGS.md in any case', () => {
    expect(readingFolderState([...files(PATHS), { path: 'readings/01_intro', dir: true }])).toEqual([
      { path: 'readings/01_intro', files: 2, hasList: true },
      { path: 'readings/02_trees', files: 1, hasList: false },
      { path: 'readings/03_empty', files: 0, hasList: false },
      { path: 'readings/04_nested', files: 0, hasList: false },
    ]);
    expect(readingFolderState(files(['readings/05/readings.md', 'readings/05/a.pdf']))[0]!.hasList).toBe(true);
    expect([...foldersWithoutList(files(PATHS))]).toEqual(['readings/02_trees']);
  });

  it('reads the folder off a check or to-do id, and links to the list in the course file view', () => {
    expect(readingListFolder('reading_list:readings/02_trees')).toBe('readings/02_trees');
    expect(readingListFolder(`materials:${MAT}:reading_list:readings/02_trees`)).toBe('readings/02_trees');
    expect(readingListFolder('withheld')).toBeNull();
    const href = readingListHref(ORG, MAT, 'readings/02_trees');
    expect(parseSearch(href.split('#')[0]!)).toMatchObject({ course: ORG, file: `${MAT}/readings/02_trees/READINGS.md` });
    expect(href.endsWith('#materials')).toBe(true);
  });
});

describe('the course materials index tree (decision 0046 amendment 1 rule 9)', () => {
  let root: HTMLElement;
  beforeEach(() => { sessionStorage.clear(); root = document.createElement('div'); document.body.appendChild(root); });
  afterEach(() => { render(null, root); root.remove(); });

  it('is a pure browser: no reading-list chip on any folder or file, open or closed (the repo page’s Files panel carries them)', async () => {
    await act(() => render(<MaterialsTree org={ORG} repo={MAT} paths={files(PATHS)} />, root));
    expect(root.textContent).not.toContain('reading list');
    await act(() => render(<MaterialsTree org={ORG} repo={MAT} paths={files(PATHS)} focus="readings/02_trees" />, root));
    expect(root.querySelector('li[data-folder="readings/02_trees"]')).not.toBeNull();
    expect(root.textContent).not.toContain('reading list');
  });
});

describe('a reading list not written yet, opened from the course side', () => {
  const course: Course = { org: ORG, name: 'Machine Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: [], meta: null };
  const LIST = 'readings/02_trees/READINGS.md';
  const cp = (file: string, write = true): CourseProps => ({
    course: { ...course, write }, loaded: { kind: 'absent' }, cohortStates: {}, now: 0, file,
    files: new StaticFiles({}, {}, { [`${ORG}/${MAT}`]: PATHS }),
  } as unknown as CourseProps);
  let root: HTMLElement | null = null;
  let gh: FakeGitHub;
  afterEach(() => { if (root) render(null, root); root?.remove(); root = null; });
  async function mount(p: CourseProps, checks: MaterialsCheck[] = []) {
    gh = new FakeGitHub().on('PUT', /\/contents\//, () => json({ content: { sha: 'b1' }, commit: { sha: 'c1' } }))
      .on('POST', '/markdown', (req: Seen) => new Response(`<p>${(req.body as { text: string }).text}</p>`, { status: 200 }));
    const env = { user: { login: 'prof', id: 1, name: 'P', email: null, avatar_url: '' }, client: new GitHubClient({ token: () => 't', fetch: gh.fetch }), files: p.files } as unknown as Env;
    // The course block names the repo, so `?file=` opens in it.
    const status = { course: { org: ORG, materials: [{ repo: MAT, state: 'ready', checks }] } };
    root = document.createElement('div');
    document.body.appendChild(root);
    await act(() => render(<EnvCtx.Provider value={env}><MaterialsIndexScreen {...p} loaded={{ kind: 'ready', status, sha: 's', stale: [] } as never} /></EnvCtx.Provider>, root!));
    for (let i = 0; i < 8; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    return root;
  }
  const button = (text: string) => [...root!.querySelectorAll('button')].find((b) => b.textContent?.trim() === text);

  it('opens straight in the editor as a New file, Back to the files going to the folder, and Save creates it', async () => {
    await mount(cp(`${MAT}/${LIST}`));
    const area = root!.querySelector<HTMLTextAreaElement>('#mf-rl')!;
    expect(area.value).toBe('');
    expect(root!.querySelector('h2.mono')!.textContent).toBe(LIST);
    expect(root!.textContent).toContain(`File: ${MAT} / ${LIST}`);
    expect(root!.textContent).toContain(LIST_TEXT.newFile);
    // The path is fixed here: the sheet's footnote about editing it is not shown.
    expect(root!.textContent).toContain(LIST_TEXT.footNewFixed);
    expect(root!.textContent).not.toContain(LIST_TEXT.footNew(MAT));
    expect(button(LIST_TEXT.detach)).toBeUndefined();
    expect(button('Edit reading list')).toBeUndefined();
    const back = [...root!.querySelectorAll<HTMLAnchorElement>('a.btn')].find((a) => a.textContent === 'Back to the files')!;
    expect(back.className).toBe('btn small quiet');
    expect(parseSearch(back.getAttribute('href')!.split('#')[0]!)).toMatchObject({ course: ORG, folder: `${MAT}/readings/02_trees` });
    await act(() => { area.value = '- Breiman (2001): Breiman.pdf\n'; area.dispatchEvent(new Event('input', { bubbles: true })); });
    await act(() => button('Save')!.click());
    for (let i = 0; i < 8; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    const put = gh.seen.filter((s: Seen) => s.method === 'PUT');
    expect(put.map((s) => s.url.split('/repos/')[1])).toEqual([`${ORG}/${MAT}/contents/${LIST}`]);
    const b = put[0]!.body as { content: string; sha?: string; message: string };
    expect([decodeBase64(b.content), b.sha, b.message]).toEqual(['- Breiman (2001): Breiman.pdf\n', undefined, `readings: edit ${LIST}, from the DSL Teaching Console`]);
  });

  it('offers no editor to someone who cannot change the course', async () => {
    await mount(cp(`${MAT}/${LIST}`, false));
    expect(root!.querySelector('#mf-rl')).toBeNull();
  });

  it('links the repo row’s suggestions chip to the repo’s settings, where they are listed (rule 10)', async () => {
    const open = (id: string): MaterialsCheck => ({ id, label: id, done: false, why: WHY, need: 'suggested' });
    await mount(cp(''), [open('reading_list:readings/02_trees'), open('syllabus')]);
    const chip = root!.querySelector<HTMLAnchorElement>('.r-title .chip.suggest')!;
    expect([chip.tagName, chip.textContent, chip.getAttribute('href'), chip.getAttribute('aria-label')]).toEqual(['A', '2 suggestions', `#materials-${MAT}`, `Open ${MAT} settings`]);
  });
});

describe('the readings folder’s suggestion', () => {
  const check: MaterialsCheck = { id: 'reading_list:readings/02_trees', label: 'readings/02_trees: reading list', done: false, why: WHY, need: 'suggested' };
  const todo: Todo = { id: `materials:${MAT}:reading_list:readings/02_trees`, kind: 'materials', check: 'reading_list', repo: MAT, text: WHY, screen: 'materials', entry: MAT, need: 'suggested', set_aside: false };
  const syllabus: Todo = { ...todo, id: `materials:${MAT}:syllabus`, check: 'syllabus', text: 'There is no SYLLABUS.md yet.' };

  it('links its to-do to the list, other materials to-dos to the repo’s settings', () => {
    expect(todoHref(todo, ORG)).toBe(readingListHref(ORG, MAT, 'readings/02_trees'));
    expect(todoHref(syllabus, ORG)).toBe(`#materials-${MAT}`);
    // A schedule entry's own reading-list to-do keeps its own screen.
    expect(todoHref({ ...todo, id: 'schedule:reading_list:readings-5', kind: 'schedule', screen: 'schedule', entry: 'readings-5' }, ORG)).toBe('#schedule-readings-5');
  });

  it('counts among the suggestions and shows with its label, why and link', () => {
    const c = { org: ORG, stages: {}, stage_why: {}, stage_need: {}, materials: [{ repo: MAT, state: 'ready', checks: [check] }], templates: [], todo: [syllabus, todo] } as unknown as CourseStatus;
    const items = courseItems(c, [], null).filter((i) => i.kind === 'todo');
    expect(suggestionsCount(items)).toBe(2);
    const row = items.find((i) => i.id === todo.id)!;
    expect([row.label, row.why, row.link]).toEqual(['readings/02_trees: reading list', WHY, { href: readingListHref(ORG, MAT, 'readings/02_trees'), label: 'Write reading list' }]);
    expect(items.find((i) => i.id === syllabus.id)!.link!.label).toBe('Open settings');
  });

  it('shows in the repo’s Settings checklist under Suggested, with the link to the list', () => {
    const out = renderToString(<MaterialsChecklist checks={[check]} at={{ org: ORG, repo: MAT }} />);
    expect(out).toContain('Suggested');
    expect(out).toContain('readings/02_trees: reading list');
    expect(out).toContain(`href="${readingListHref(ORG, MAT, 'readings/02_trees').replace(/&/g, '&amp;')}"`);
    expect(out).toContain('Write reading list');
  });
});
