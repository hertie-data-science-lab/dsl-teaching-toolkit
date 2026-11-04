// @vitest-environment happy-dom
// A semester's Released materials (`?cohort=<org>#materials`): its materials repo with the tree,
// a file opening in the viewer, the released materials that differ from their source files in
// their own folded panel (three-way, filtered by step, Release again and Keep for future semesters, each
// folder's differing files), Release by hand below for those who can change the course (adding a course repo sets its
// topic), the empty states, the nav and the route that keeps it apart from the course's
// `#materials` / `#materials-<repo>`.

import { createHash } from 'node:crypto';
import { render } from 'preact';
import { render as html } from 'preact-render-to-string';
import { act } from 'preact/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient } from '../src/github/client';
import type { Course } from '../src/model/discovery';
import type { Loaded } from '../src/model/status';
import type { Problem, Release, Status } from '../src/model/types';
import { COHORT_SCREENS, COURSE_SCREENS, RELEASED_MATERIALS, parseHash, parseSearch, screenOf } from '../src/router';
import { SemesterMaterialsScreen, outOfStep } from '../src/screens/SemesterMaterials';
import type { Files } from '../src/model/files';
import type { CohortProps } from '../src/screens/types';
import { Sidenav } from '../src/ui/shell';
import { fileViewHref } from '../src/ui/MaterialsTree';
import { FakeGitHub, fileBody } from './fake';
import example from './fixtures/status.example.json';
import { StaticFiles } from './staticFiles';

const COURSE_ORG = 'hertie-dsl-demo-course-e1234';
const ORG = 'hertie-dsl-demo-f2026';
const PATHS = ['SYLLABUS.md', 'lectures/05_trees/notes.md', 'lectures/05_trees/slides.pdf', 'labs/lab-1/lab.ipynb'];
const blob = (path: string) => ({ path, mode: '100644', type: 'blob', sha: `sha-${path}`, size: 10 });
const cohort = { org: ORG, term: 'f2026', termLabel: 'Fall 2026' };
const course: Course = { org: COURSE_ORG, name: 'Machine Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: [cohort], meta: null };
const STATUS = example as unknown as Status;

const rel = (id: string, path: string, state: Release['state'] = 'released'): Release => ({
  id, when: '2026-09-10T10:00:00+02:00', kind: 'lecture', number: 5, title: id, state, tbc: false, show_on_site: true,
  source: { repo: 'course-materials-f2026', path }, dest: { repo: 'materials', path },
});
const edited: Problem = {
  id: 'schedule:s5:DIVERGED_RELEASED:cccccccc', scope: 'semester', stage: 'K4', kind: 'DIVERGED_RELEASED', release: 's5', aside: true,
  label: 'Released copy edited', text: 'The released copy of Lecture 5 was edited in the semester: 1 file changed.', stops: '', bites: 'later',
};
const statusWith = (releases: Release[], problems: Problem[] = []): Loaded => ({ kind: 'ready', sha: 's', stale: [], status: { ...STATUS, releases, problems } });
const RELEASED = statusWith([rel('s5', 'lectures/05_trees'), rel('lab-1', 'labs/lab-1'), rel('s6', 'lectures/06_nets', 'planned')], [edited]);

const files = (trees: Record<string, string[]> = { [`${ORG}/materials`]: PATHS }) =>
  new StaticFiles({}, {}, trees, {
    [ORG]: [{ name: 'materials', pushed_at: '2026-09-22T14:38:46Z' }, { name: 'assignment-2-anna-a' }],
    [COURSE_ORG]: [{ name: 'course-materials-f2026' }, { name: 'assignment-3-f2026' }, { name: 'guest-lectures' }],
  });
// The course copy and the semester's two branches for the step panel: notes.md edited in the
// semester (ahead), lab.ipynb changed in the course and new.md added there (behind), slides.pdf
// as released. `course` overrides a course blob's sha (`undefined` drops the file).
const COURSE_PATHS = ['lectures/05_trees/notes.md', 'lectures/05_trees/slides.pdf', 'labs/lab-1/lab.ipynb', 'labs/lab-1/new.md', 'labs/lab-1/solutions/key.md'];
function stepFiles(course: Record<string, string | undefined> = {}, semesterExtra: string[] = []): Files {
  const shas: Record<string, string> = { [`${ORG}/materials/lectures/05_trees/notes.md`]: 'edited', [`${COURSE_ORG}/course-materials-f2026/labs/lab-1/lab.ipynb`]: 'new' };
  const paths = COURSE_PATHS.filter((x) => !(x in course) || course[x] !== undefined);
  for (const [k, v] of Object.entries(course)) if (v) shas[`${COURSE_ORG}/course-materials-f2026/${k}`] = v;
  const up = new StaticFiles({}, {}, { [`${ORG}/materials`]: PATHS });
  const live = new StaticFiles({}, {}, { [`${ORG}/materials`]: [...PATHS, ...semesterExtra], [`${COURSE_ORG}/course-materials-f2026`]: paths }, {
    [ORG]: [{ name: 'materials', pushed_at: '2026-09-22T14:38:46Z' }], [COURSE_ORG]: [{ name: 'course-materials-f2026' }],
  }, {}, shas);
  return Object.assign(Object.create(live), { tree: (o: string, r: string, ref?: string) => (ref ? up.tree(o, r) : live.tree(o, r)) });
}
const props = (over: Partial<CohortProps> = {}): CohortProps => ({ course, cohort, loaded: RELEASED, files: files(), now: Date.parse('2026-09-23T10:00:00Z'), ...over });
const text = (v: preact.VNode) => html(v).replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/\s+/g, ' ');

let root: HTMLElement | null = null;
beforeEach(() => sessionStorage.clear());
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
});

async function mount(view: preact.VNode, gh = new FakeGitHub()) {
  gh
    .on('GET', `/repos/${ORG}/materials/git/trees/HEAD?recursive=1`, { sha: 't', truncated: false, tree: PATHS.map(blob) })
    .on('GET', `/repos/${ORG}/materials/contents/lectures/05_trees/notes.md`, fileBody('lectures/05_trees/notes.md', '# Trees'))
    .on('POST', '/markdown', () => new Response('<h1>Trees</h1>', { status: 200 }));
  const env = { user: { login: 'prof', id: 1, name: 'P', email: null, avatar_url: '' }, client: new GitHubClient({ token: () => 't', fetch: gh.fetch }), ops: { open: () => {} } } as unknown as Env;
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(() => render(<EnvCtx.Provider value={env}>{view}</EnvCtx.Provider>, root!));
  for (let i = 0; i < 6; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return root;
}

describe('Released materials', () => {
  it('lists the semester’s materials repo with its chip, last change and tree', async () => {
    const el = await mount(<SemesterMaterialsScreen {...props()} />);
    expect(el.querySelector('h2.h1')!.textContent).toContain('Released materials');
    const row = el.querySelector('ul.rows > li')!;
    expect(row.querySelector('.r-title')!.textContent).toContain('materials');
    expect(row.querySelector('.chip')!.textContent).toBe('2 releases');
    expect(row.textContent).toContain('Last change');
    const names = [...row.querySelectorAll('.materials-tree .ft-dir > details > summary')].map((s) => s.textContent);
    expect(names.some((n) => n?.startsWith('lectures'))).toBe(true);
    expect(names.some((n) => n?.startsWith('labs'))).toBe(true);
  });

  it('lists the semester’s effective default destination, where a release naming no repo lands', () => {
    const sem = { ...STATUS.semester!, defaults: { ...STATUS.semester!.defaults, materials_repo: { value: 'course-files', source: 'semester' as const } } };
    const unnamed: Release = { ...rel('s5', 'lectures/05_trees'), dest: null };
    const loaded: Loaded = { kind: 'ready', sha: 's', stale: [], status: { ...STATUS, semester: sem, releases: [unnamed], problems: [] } };
    const t = text(<SemesterMaterialsScreen {...props({ loaded, files: files({ [`${ORG}/course-files`]: PATHS }) })} />);
    expect(t).toContain('course-files 1 release');
    expect(t).not.toContain('materials nothing released');
  });

  it('opens each file in the viewer on the semester’s page', async () => {
    const el = await mount(<SemesterMaterialsScreen {...props()} />);
    const name = el.querySelector<HTMLAnchorElement>('.ft-file a.ft-name')!;
    expect(name.getAttribute('href')).toBe(fileViewHref(ORG, 'materials', 'SYLLABUS.md', 'cohort'));
    expect(name.getAttribute('href')).toBe(`?cohort=${ORG}&file=${encodeURIComponent('materials/SYLLABUS.md')}#materials`);
  });

  it('shows a file open in the viewer beside the repo’s tree', async () => {
    const el = await mount(<SemesterMaterialsScreen {...props({ file: 'materials/lectures/05_trees/notes.md' })} />);
    const split = el.querySelector('.materials-split')!;
    expect(split.querySelector('.materials-tree a[aria-current]')!.textContent).toBe('notes.md');
    expect(split.querySelector('.materials-file .md-body')!.innerHTML).toContain('Trees');
    expect(el.querySelector(`a[href="?cohort=${ORG}#materials"]`)!.textContent).toBe('All released materials');
  });

  it('links no Release page from the tree rows or the edited copies', async () => {
    const el = await mount(<SemesterMaterialsScreen {...props({ folder: 'materials/lectures/05_trees' })} />);
    expect(el.querySelector('a[href^="#release-"]')).toBeNull();
  });

  it('folds Released materials this semester first, then Release by hand from the course repos', async () => {
    const el = await mount(<SemesterMaterialsScreen {...props()} />);
    const folds = [...el.querySelectorAll<HTMLDetailsElement>(':scope > details.panel-fold')];
    expect(folds.map((d) => d.querySelector('summary h2')!.firstChild!.textContent!.trim())).toEqual(['Released materials this semester', 'Release by hand']);
    expect(folds.every((d) => d.open)).toBe(true);
    expect(folds[0].querySelector('ul.rows > li .materials-tree')).not.toBeNull();
    // From: the course's materials repos, then its other repos (no template), then Add.
    const from = el.querySelector<HTMLSelectElement>('#rbh-from')!;
    expect([...from.options].map((o) => o.value)).toEqual(['course-materials-f2026', 'guest-lectures', '__add']);
    expect(from.value).toBe('course-materials-f2026');
  });

  it('draws no Release by hand for a viewer who cannot change the course', async () => {
    const el = await mount(<SemesterMaterialsScreen {...props({ course: { ...course, write: false } })} />);
    expect(el.querySelector('#rbh-from')).toBeNull();
    expect(el.querySelectorAll('details.panel-fold')).toHaveLength(1);
  });

  it('adds a course repo by setting its materials topic, as Treat as course materials does', async () => {
    const gh = new FakeGitHub()
      .on('GET', `/repos/${COURSE_ORG}/guest-lectures/topics`, { names: ['python'] })
      .on('PUT', `/repos/${COURSE_ORG}/guest-lectures/topics`, { names: ['python', 'dsl-materials'] });
    const el = await mount(<SemesterMaterialsScreen {...props()} />, gh);
    const from = el.querySelector<HTMLSelectElement>('#rbh-from')!;
    await act(() => {
      from.value = '__add';
      from.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await act(() => [...document.querySelectorAll<HTMLButtonElement>('.modal .btn')].find((b) => b.textContent === 'Add')!.click());
    for (let i = 0; i < 6; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(gh.seen.find((x) => x.method === 'PUT')!.body).toEqual({ names: ['python', 'dsl-materials'] });
    document.querySelectorAll('.modal-scrim').forEach((x) => x.remove());
  });

  it('works out each released folder out of step with the course, three-way', () => {
    const st = (RELEASED as Extract<Loaded, { kind: 'ready' }>).status;
    expect(outOfStep(st, stepFiles(), COURSE_ORG, ORG, 'materials').map((x) => [x.path, x.step, x.files])).toEqual([
      ['lectures/05_trees', 'ahead', [{ path: 'lectures/05_trees/notes.md', state: 'ahead', from: 'lectures/05_trees/notes.md' }]],
      ['labs/lab-1', 'behind', [{ path: 'labs/lab-1/lab.ipynb', state: 'behind', from: 'labs/lab-1/lab.ipynb' }, { path: 'labs/lab-1/new.md', state: 'not_released', from: 'labs/lab-1/new.md' }]],
    ]);
    // solutions/key.md, withheld by default, is no difference; nor, once gone, is new.md.
    expect(outOfStep(st, stepFiles({ 'labs/lab-1/new.md': undefined }), COURSE_ORG, ORG, 'materials')[1].files.map((f) => f.path)).toEqual(['labs/lab-1/lab.ipynb']);
    // Edited in the semester and changed in the course: diverged.
    expect(outOfStep(st, stepFiles({ 'lectures/05_trees/slides.pdf': 'new' }), COURSE_ORG, ORG, 'materials')[0].step).toBe('diverged');
    expect(outOfStep(st, files(), COURSE_ORG, ORG, 'materials')).toEqual([]);
  });

  it('counts a file only the semester copy has as ahead, added in the semester, and lists it with its Open menu', async () => {
    const st = (RELEASED as Extract<Loaded, { kind: 'ready' }>).status;
    // slides.pdf as released, notes.md edited: in step but for the added files.
    const f = stepFiles({ 'lectures/05_trees/notes.md': undefined }, ['lectures/05_trees/extra.md', 'lectures/05_trees/.DS_Store']);
    const lec = outOfStep(st, f, COURSE_ORG, ORG, 'materials')[0];
    expect(lec.step).toBe('ahead');
    expect(lec.files).toEqual([{ path: 'lectures/05_trees/notes.md', state: 'ahead', added: true }, { path: 'lectures/05_trees/extra.md', state: 'ahead', added: true }]);
    // Course-changed plus semester-added: diverged.
    expect(outOfStep(st, stepFiles({}, ['labs/lab-1/mine.md']), COURSE_ORG, ORG, 'materials')[1].step).toBe('diverged');
    const el = await mount(<SemesterMaterialsScreen {...props({ files: f })} />);
    const row = el.querySelector<HTMLElement>('details.out-of-step ul.rows > li')!;
    expect(row.querySelector('.r-sub.footnote')!.textContent).toBe('2 added in the semester');
    const extra = [...row.querySelectorAll('details.step-files li')].find((li) => li.querySelector('code')!.textContent === 'extra.md')!;
    expect(extra.querySelector('.chip')!.textContent).toBe('ahead');
    expect(extra.querySelector('a.ft-name')!.getAttribute('href')).toBe(fileViewHref(ORG, 'materials', 'lectures/05_trees/extra.md', 'cohort'));
    expect(extra.querySelector('.file-menu')).not.toBeNull();
  });

  it('lists the released folders out of step in their own folded panel, filtered by step', async () => {
    const el = await mount(<SemesterMaterialsScreen {...props({ files: stepFiles() })} />);
    const folds = [...el.querySelectorAll<HTMLDetailsElement>(':scope > details.panel-fold')];
    expect(folds.map((d) => d.querySelector('summary h2')!.firstChild!.textContent!.trim())).toEqual(['Released materials this semester', 'Released materials that differ from their source files', 'Release by hand']);
    const panel = el.querySelector<HTMLDetailsElement>('details.out-of-step')!;
    expect(panel.open).toBe(false);
    const sum = panel.querySelector('summary h2')!;
    expect(sum.querySelector('.step-bang')!.textContent).toBe('!');
    // Counts files: notes.md, then lab.ipynb and new.md.
    expect(sum.querySelector('.chip.amber')!.textContent).toBe('3 released files differ');
    expect(sum.querySelector('.hint-pop')!.textContent).toMatch(/^For each released folder the toolkit compares the course copy with the semester’s and lists every file that differs\. Nothing changes until you choose\./);
    expect(sum.querySelector('.hint-pop')!.textContent).toContain('Nothing changes until you choose.');
    // The engine's DIVERGED_RELEASED line is the Dashboard's, not this page's.
    expect(el.textContent).not.toContain('The released copy of Lecture 5 was edited');
    const tabs = [...panel.querySelectorAll<HTMLButtonElement>('.seg[role="tablist"] button[role="tab"]')];
    expect(tabs.map((t) => t.textContent)).toEqual(['All 2', 'Edited in semester 1', 'Edited in course 1', 'Both 0']);
    const rows = () => [...panel.querySelectorAll<HTMLElement>('ul.rows > li')];
    expect(rows().map((li) => li.querySelector('.r-title code')!.textContent)).toEqual(['lectures/05_trees/', 'labs/lab-1/']);
    expect(rows().map((li) => li.querySelector('.r-title .chip')!.textContent)).toEqual(['ahead', 'behind']);
    expect(rows().map((li) => li.querySelector('.r-sub.footnote')!.textContent)).toEqual(['1 changed', '1 changed, 1 added in the course']);
    expect(rows().map((li) => [...li.querySelectorAll('.r-side button.btn.small.quiet')].map((b) => b.textContent))).toEqual([
      ['Release again', 'Keep for future semesters'], ['Release again', 'Keep for future semesters'],
    ]);
    await act(() => tabs[2].click());
    expect(tabs[2].getAttribute('aria-selected')).toBe('true');
    expect(rows().map((li) => li.querySelector('.r-title code')!.textContent)).toEqual(['labs/lab-1/']);
    await act(() => tabs[0].click());
    // Each folder's differing files: the semester copy's file row, its chip, and Compare on GitHub when edited there.
    const [lec, lab] = rows().map((li) => li.querySelector<HTMLDetailsElement>('details.step-files')!);
    expect(lec.querySelector('summary')!.textContent).toBe('1 file');
    const notes = lec.querySelector('li')!;
    expect(notes.querySelector('a.ft-name')!.getAttribute('href')).toBe(fileViewHref(ORG, 'materials', 'lectures/05_trees/notes.md', 'cohort'));
    expect(notes.querySelector('a.ft-name code')!.textContent).toBe('notes.md');
    expect(notes.querySelector('.file-menu')).not.toBeNull();
    expect(notes.querySelector('.chip')!.textContent).toBe('ahead');
    // Compare on GitHub opens at the file's own diff: GitHub's anchor, the path's sha256.
    const anchor = createHash('sha256').update('lectures/05_trees/notes.md').digest('hex');
    for (let i = 0; i < 4; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(notes.querySelector<HTMLAnchorElement>('a.textlink[href*="compare"]')!.getAttribute('href')).toBe(`https://github.com/${ORG}/materials/compare/upstream...main#diff-${anchor}`);
    expect(notes.querySelector('details.file-diff-fold > summary')!.textContent).toBe('Diff');
    const labRows = [...lab.querySelectorAll('li')];
    expect(labRows.map((li) => li.querySelector('code')!.textContent)).toEqual(['lab.ipynb', 'new.md']);
    expect(labRows[0].querySelector('a[href*="compare"]')).toBeNull();
    // Not in the semester yet: no file row to open.
    expect(labRows[1].querySelector('a.ft-name')).toBeNull();
    expect(labRows[1].querySelector('.chip')!.textContent).toBe('not released');
  });

  it('opens Release again for the folder’s release, and Keep for future semesters', async () => {
    const open = vi.fn();
    root = document.createElement('div');
    document.body.appendChild(root);
    const env = { user: { login: 'prof', id: 1, name: 'P', email: null, avatar_url: '' }, client: new GitHubClient({ token: () => 't', fetch: new FakeGitHub().fetch }), ops: { open } } as unknown as Env;
    await act(() => render(<EnvCtx.Provider value={env}><SemesterMaterialsScreen {...props({ files: stepFiles() })} /></EnvCtx.Provider>, root!));
    const buttons = [...root.querySelectorAll<HTMLButtonElement>('details.out-of-step ul.rows > li')[1].querySelectorAll<HTMLButtonElement>('.r-side button')];
    await act(() => buttons[0].click());
    expect(open.mock.calls[0][0]).toMatchObject({ op: 'release.entry', name: 'Release again', args: { entry: 'lab-1' } });
    await act(() => buttons[1].click());
    expect(open.mock.calls[1][0]).toMatchObject({ op: 'release.propagate_back', name: 'Keep for future semesters' });
  });

  it('draws no differing-materials panel when every released folder is in step', () => {
    const out = html(<SemesterMaterialsScreen {...props()} />);
    expect(out).not.toContain('out-of-step');
    expect(out).not.toContain('Released materials that differ from their source files');
  });

  it('says when nothing is released yet, no repo exists yet, or the files cannot be read', () => {
    const none = text(<SemesterMaterialsScreen {...props({ loaded: statusWith([rel('s6', 'x', 'planned')]), files: files({}) })} />);
    expect(none).toContain('Nothing released yet');
    expect(none).toContain('No such repo in the semester yet');
    const unread: StaticFiles = files();
    unread.tree = () => ({ kind: 'error', message: 'boom' });
    expect(text(<SemesterMaterialsScreen {...props({ files: unread })} />)).toContain('Could not read the repo’s files.');
    expect(text(<SemesterMaterialsScreen {...props({ loaded: { kind: 'error', message: 'nope' } })} />)).toContain('Could not read the semester\'s status: nope');
  });
});

describe('the semester’s #materials route', () => {
  it('is Released materials under ?cohort=, the course index otherwise, and the repo page with an entry', () => {
    const at = (url: string) => {
      const [q, h] = url.split('#');
      return screenOf(parseSearch(q), parseHash(`#${h}`));
    };
    expect(at(`?cohort=${ORG}#materials`)).toBe(RELEASED_MATERIALS);
    expect(at(`?cohort=${ORG}&file=materials%2FSYLLABUS.md#materials`)).toBe(RELEASED_MATERIALS);
    expect(at(`?course=${COURSE_ORG}#materials`)).toBe('materials');
    expect(at(`?course=${COURSE_ORG}#released-materials`)).toBe('materials');
    expect(at(`?cohort=${ORG}#materials-course-materials-f2026`)).toBe('materials');
    expect(parseHash('#materials-course-materials-f2026')).toEqual({ screen: 'materials', entry: 'course-materials-f2026' });
    expect(COHORT_SCREENS[RELEASED_MATERIALS]).toBe('materials');
    expect(RELEASED_MATERIALS in COURSE_SCREENS).toBe(false);
  });

  it('lights the semester’s Materials, not Course materials, and the course link names the course', () => {
    const nav = html(<Sidenav courses={[course]} course={course} cohort={cohort} cohortStates={{}} current="materials" />);
    expect(nav).toContain('href="#materials" aria-current="page"');
    expect(nav).toContain(`href="?course=${COURSE_ORG}#materials"`);
    expect(nav).not.toContain(`href="?course=${COURSE_ORG}#materials" aria-current`);
    // On the course index (no semester open) only Course materials is lit.
    const onCourse = html(<Sidenav courses={[course]} course={course} cohortStates={{}} current="materials" />);
    expect(onCourse).not.toContain('href="#materials" aria-current');
    expect(onCourse).not.toContain(`href="?cohort=${ORG}#materials" aria-current`);
  });
});
