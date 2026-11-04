// @vitest-environment happy-dom
// A semester's Released materials (`?cohort=<org>#materials`): its materials repo with the tree,
// a file opening in the viewer, the released materials that differ from their source files in
// their own folded panel (three-way, filtered by step, each folder a fold of its differing files,
// each file with Re-release, Open and Diff, one PR into source), Release by hand below for those who can change the course (adding a course repo sets its
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
import { SemesterMaterialsScreen } from '../src/screens/SemesterMaterials';
import { outOfStep } from '../src/model/releaseState';
import { editedIn } from '../src/screens/CourseIndex';
import type { Files, TreeState } from '../src/model/files';
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
// as released. `course` overrides a course blob's sha (`undefined` drops the file, a path not
// listed adds it); `upstream`, what the semester's `upstream` branch has.
const COURSE_PATHS = ['lectures/05_trees/notes.md', 'lectures/05_trees/slides.pdf', 'labs/lab-1/lab.ipynb', 'labs/lab-1/new.md', 'labs/lab-1/solutions/key.md'];
function stepFiles(course: Record<string, string | undefined> = {}, semesterExtra: string[] = [], semesterGone: string[] = [], upstream: string[] = PATHS): Files {
  const shas: Record<string, string> = { [`${ORG}/materials/lectures/05_trees/notes.md`]: 'edited', [`${COURSE_ORG}/course-materials-f2026/labs/lab-1/lab.ipynb`]: 'new' };
  const paths = [...COURSE_PATHS.filter((x) => !(x in course) || course[x] !== undefined), ...Object.keys(course).filter((x) => course[x] && !COURSE_PATHS.includes(x))];
  for (const [k, v] of Object.entries(course)) if (v) shas[`${COURSE_ORG}/course-materials-f2026/${k}`] = v;
  const up = new StaticFiles({}, {}, { [`${ORG}/materials`]: upstream });
  const live = new StaticFiles({}, {}, { [`${ORG}/materials`]: [...PATHS.filter((x) => !semesterGone.includes(x)), ...semesterExtra], [`${COURSE_ORG}/course-materials-f2026`]: paths }, {
    [ORG]: [{ name: 'materials', pushed_at: '2026-09-22T14:38:46Z' }], [COURSE_ORG]: [{ name: 'course-materials-f2026' }],
  }, {}, shas);
  return Object.assign(Object.create(live), { tree: (o: string, r: string, ref?: string) => (ref ? up.tree(o, r) : live.tree(o, r)) });
}
const props = (over: Partial<CohortProps> = {}): CohortProps => ({ course, cohort, loaded: RELEASED, files: files(), now: Date.parse('2026-09-23T10:00:00Z'), ...over });
const text = (v: preact.VNode) => html(v).replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/\s+/g, ' ');

/** A differing file row's Diff button. */
const diffOf = (li: HTMLElement) => [...li.querySelectorAll<HTMLButtonElement>('.dif-right > button')].find((b) => b.textContent === 'Diff')!;

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
    const extra = [...el.querySelectorAll<HTMLElement>('details.out-of-step li.dif-file')].find((li) => li.querySelector('.dif-name')!.textContent === 'extra.md')!;
    expect(extra.querySelector('.chip')!.textContent).toBe('ahead');
    expect(extra.querySelector('.split .split-main')!.getAttribute('href')).toBe(`https://github.com/${ORG}/materials/blob/HEAD/lectures/05_trees/extra.md`);
  });

  it('lists a file released, then removed in the semester: edited in semester, Overwrite restores it (R1)', async () => {
    const st = (RELEASED as Extract<Loaded, { kind: 'ready' }>).status;
    const f = stepFiles({}, [], ['lectures/05_trees/slides.pdf']);
    expect(outOfStep(st, f, COURSE_ORG, ORG, 'materials')[0].files.map((x) => [x.path, x.state])).toEqual([
      ['lectures/05_trees/notes.md', 'ahead'], ['lectures/05_trees/slides.pdf', 'removed'],
    ]);
    const open = vi.fn();
    root = document.createElement('div');
    document.body.appendChild(root);
    const env = { user: { login: 'prof', id: 1, name: 'P', email: null, avatar_url: '' }, client: new GitHubClient({ token: () => 't', fetch: new FakeGitHub().fetch }), ops: { open } } as unknown as Env;
    await act(() => render(<EnvCtx.Provider value={env}><SemesterMaterialsScreen {...props({ files: f })} /></EnvCtx.Provider>, root!));
    const panel = root.querySelector<HTMLElement>('details.out-of-step')!;
    expect(panel.querySelector('summary .chip.amber')!.textContent).toBe('4 released files differ');
    expect([...panel.querySelectorAll('[role="tab"]')].map((t) => t.textContent)).toEqual(['All 4', 'Edited in semester 2', 'Edited in course 2', 'Both 0']);
    const slides = [...panel.querySelectorAll<HTMLElement>('li.dif-file')].find((li) => li.querySelector('.dif-name')!.textContent === 'slides.pdf')!;
    const chip = slides.querySelector<HTMLElement>('.chip')!;
    expect([chip.textContent, chip.classList.contains('semester'), chip.title]).toEqual(['removed in semester', true, 'Released before, then removed in the semester; a plain release changes nothing. Overwrite restores the course version.']);
    const btn = (label: string) => [...slides.querySelectorAll<HTMLButtonElement>('.dif-right > button.btn')].find((b) => b.textContent === label)!;
    expect([btn('Re-release').disabled, btn('Re-release').title]).toEqual([true, 'The semester removed it; Overwrite restores the course version.']);
    expect([btn('PR into source').disabled, btn('PR into source').title]).toEqual([true, 'Nothing to carry back: the file is gone on the semester side.']);
    // Nothing to open on the semester side.
    expect(slides.querySelector('.split')).toBeNull();
    expect(btn('Overwrite').disabled).toBe(false);
    await act(() => btn('Overwrite').click());
    expect(open.mock.calls[0][0]).toMatchObject({
      op: 'release.adhoc', confirm: 'Replaces the semester’s copy of this file with the course version; its edit is lost.',
      args: { course_source_path: 'lectures/05_trees/slides.pdf', semester_dest_path: 'lectures/05_trees/slides.pdf', replace: true },
    });
  });

  // R1b: a copy released by hand (`release.adhoc`, no schedule entry) is on `upstream` all the same.
  const HAND = 'labs/12_tutorial-presentations/README.md';
  const handFiles = (more: string[] = [], live: string[] = []) => stepFiles({ [HAND]: `sha-${HAND}` }, live, [], [...PATHS, HAND, ...more]);

  it('lists a file released by hand, then removed in the semester, beside the scheduled ones, with Overwrite (R1b)', async () => {
    const st = (RELEASED as Extract<Loaded, { kind: 'ready' }>).status;
    const rows = outOfStep(st, handFiles(), COURSE_ORG, ORG, 'materials');
    expect(rows.map((x) => [x.path, x.r?.id, x.src, x.step])).toEqual([
      ['lectures/05_trees', 's5', 'course-materials-f2026', 'ahead'], ['labs/lab-1', 'lab-1', 'course-materials-f2026', 'behind'],
      ['labs/12_tutorial-presentations', undefined, 'course-materials-f2026', 'ahead'],
    ]);
    expect(rows[2].files).toEqual([{ path: HAND, state: 'removed', from: HAND }]);
    // The repo's own top-level files on `upstream` (SYLLABUS.md here) are no release's.
    expect(rows.flatMap((x) => x.files).some((f) => f.path === 'SYLLABUS.md')).toBe(false);
    const open = vi.fn();
    root = document.createElement('div');
    document.body.appendChild(root);
    const env = { user: { login: 'prof', id: 1, name: 'P', email: null, avatar_url: '' }, client: new GitHubClient({ token: () => 't', fetch: new FakeGitHub().fetch }), ops: { open } } as unknown as Env;
    await act(() => render(<EnvCtx.Provider value={env}><SemesterMaterialsScreen {...props({ files: handFiles() })} /></EnvCtx.Provider>, root!));
    const panel = root.querySelector<HTMLElement>('details.out-of-step')!;
    // The count chip and the tabs count the scheduled and the hand-released files alike.
    expect(panel.querySelector('summary .chip.amber')!.textContent).toBe('4 released files differ');
    expect([...panel.querySelectorAll('[role="tab"]')].map((t) => t.textContent)).toEqual(['All 4', 'Edited in semester 2', 'Edited in course 2', 'Both 0']);
    const folders = [...panel.querySelectorAll<HTMLElement>('li.dif-folder')];
    expect(folders.map((li) => li.querySelector('summary.dif-fhead')!.textContent)).toEqual(['lectures/05_trees/', 'labs/lab-1/', 'labs/12_tutorial-presentations/ released by hand']);
    const hand = folders[2].querySelector<HTMLElement>('li.dif-file')!;
    expect(hand.querySelector('.dif-name')!.textContent).toBe('README.md');
    expect(hand.querySelector('.chip')!.textContent).toBe('removed in semester');
    const btn = [...hand.querySelectorAll<HTMLButtonElement>('.dif-right > button.btn')].find((b) => b.textContent === 'Overwrite')!;
    expect(btn.disabled).toBe(false);
    await act(() => btn.click());
    expect(open.mock.calls[0][0]).toMatchObject({
      op: 'release.adhoc', args: { course_source_repo: 'course-materials-f2026', course_source_path: HAND, semester_dest_repo: 'materials', semester_dest_path: HAND, replace: true },
    });
  });

  it('finds a hand-released file’s source in whichever course repo has it, asking for every candidate’s tree (R1b)', () => {
    const st = (RELEASED as Extract<Loaded, { kind: 'ready' }>).status;
    // A newer course repo first, its tree still loading or absent; the older one has the file.
    const status = { ...st, course: { ...st.course!, materials: [{ repo: 'course-materials-f2026', state: 'ready' }, { repo: 'course-materials-s2027', state: 'ready' }] } };
    const asked: string[] = [];
    const withNewer = (newer: TreeState) => {
      const base = handFiles();
      return Object.assign(Object.create(base), {
        tree: (o: string, r: string, ref?: string) => (asked.push(r), r === 'course-materials-s2027' ? newer : base.tree(o, r, ref)),
      }) as Files;
    };
    for (const newer of [{ kind: 'loading' }, { kind: 'absent' }] as TreeState[]) {
      const hand = outOfStep(status, withNewer(newer), COURSE_ORG, ORG, 'materials').find((x) => !x.r)!;
      expect([newer.kind, hand.src, hand.files]).toEqual([newer.kind, 'course-materials-f2026', [{ path: HAND, state: 'removed', from: HAND }]]);
    }
    expect(asked).toContain('course-materials-s2027');
    // Only the repo still loading could have it: the folder waits for its tree.
    const only = { ...status, course: { ...status.course, materials: [{ repo: 'course-materials-s2027', state: 'ready' }] }, releases: [] };
    expect(outOfStep(only, withNewer({ kind: 'loading' }), COURSE_ORG, ORG, 'materials')).toEqual([]);
  });

  it('lists a hand-released file the course dropped, and one the semester added beside it, as only in the semester (R1b)', () => {
    const st = (RELEASED as Extract<Loaded, { kind: 'ready' }>).status;
    const old = 'labs/12_tutorial-presentations/old.md', mine = 'labs/12_tutorial-presentations/mine.md';
    const rows = outOfStep(st, handFiles([old], [old, mine]), COURSE_ORG, ORG, 'materials');
    expect(rows[2].files).toEqual([{ path: HAND, state: 'removed', from: HAND }, { path: old, state: 'ahead', added: true }, { path: mine, state: 'ahead', added: true }]);
    // Gone on both sides: nothing to list.
    expect(outOfStep(st, handFiles([old]), COURSE_ORG, ORG, 'materials')[2].files.map((f) => f.path)).toEqual([HAND]);
    // The course index chip counts them with the rest: notes.md edited, README.md removed, old.md and mine.md only in the semester.
    expect(editedIn(handFiles([old], [old, mine]), COURSE_ORG, 'course-materials-f2026', ORG, RELEASED)).toBe(4);
  });

  it('counts, for the course index chip, removed and semester-only files with the edited ones (R2)', () => {
    expect(editedIn(stepFiles(), COURSE_ORG, 'course-materials-f2026', ORG, RELEASED)).toBe(1);
    // notes.md edited, slides.pdf removed, extra.md added under the released lectures/05_trees.
    expect(editedIn(stepFiles({}, ['lectures/05_trees/extra.md'], ['lectures/05_trees/slides.pdf']), COURSE_ORG, 'course-materials-f2026', ORG, RELEASED)).toBe(3);
  });

  it('lists the differing files flat under each folder, in their own folded panel, filtered by step', async () => {
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
    // The engine's DIVERGED_RELEASED line is the Dashboard's, not this page's.
    expect(el.textContent).not.toContain('The released copy of Lecture 5 was edited');
    const tabs = [...panel.querySelectorAll<HTMLButtonElement>('.seg[role="tablist"] button[role="tab"]')];
    // The tabs count files; a file the course added is edited in the course.
    expect(tabs.map((t) => t.textContent)).toEqual(['All 3', 'Edited in semester 1', 'Edited in course 2', 'Both 0']);
    const folders = () => [...panel.querySelectorAll<HTMLElement>('ul.dif-list > li.dif-folder')];
    const names = (li: HTMLElement) => [...li.querySelectorAll('li.dif-file .dif-name')].map((c) => c.textContent);
    // Each folder its own fold, open at first, headed by its path alone: no chip, no buttons.
    expect(folders().map((li) => li.querySelector<HTMLDetailsElement>(':scope > details.fold')!.open)).toEqual([true, true]);
    expect(folders().map((li) => li.querySelector('summary.dif-fhead')!.textContent)).toEqual(['lectures/05_trees/', 'labs/lab-1/']);
    expect(folders().map((li) => li.querySelector('summary.dif-fhead .chip, summary.dif-fhead button'))).toEqual([null, null]);
    expect(folders().map(names)).toEqual([['notes.md'], ['lab.ipynb', 'new.md']]);
    await act(() => tabs[2].click());
    expect(tabs[2].getAttribute('aria-selected')).toBe('true');
    expect(folders().map(names)).toEqual([['lab.ipynb', 'new.md']]);
    await act(() => tabs[1].click());
    expect(folders().map(names)).toEqual([['notes.md']]);
    await act(() => tabs[0].click());
    const [notes, lab, added] = [...panel.querySelectorAll<HTMLElement>('li.dif-file')];
    expect(notes.querySelector('.chip')!.textContent).toBe('ahead');
    // Open: the semester copy on GitHub, the arrow a menu of the ways to open the file.
    expect(notes.querySelector('.split .split-main')!.getAttribute('href')).toBe(`https://github.com/${ORG}/materials/blob/HEAD/lectures/05_trees/notes.md`);
    expect(notes.querySelector('.split .split-caret')!.getAttribute('aria-haspopup')).toBe('menu');
    expect(lab.querySelector('.chip')!.textContent).toBe('behind');
    // Not in the semester yet: nothing to open, its course copy still diffs.
    expect(added.querySelector('.chip')!.textContent).toBe('not released');
    expect(added.querySelector('.split')).toBeNull();
    expect(diffOf(added).disabled).toBe(false);
  });

  it('draws a file’s diff under its row, headed, with its line counts once read, and greys Diff for a binary file', async () => {
    const st = (RELEASED as Extract<Loaded, { kind: 'ready' }>).status;
    // slides.pdf changed in the course: behind, binary.
    const f = stepFiles({ 'lectures/05_trees/slides.pdf': 'new' }) as StaticFiles;
    f.put(COURSE_ORG, 'course-materials-f2026', 'lectures/05_trees/notes.md', undefined, 'a\nb\nc\n');
    f.put(ORG, 'materials', 'lectures/05_trees/notes.md', undefined, 'a\nB\nc\nd\n');
    expect(outOfStep(st, f, COURSE_ORG, ORG, 'materials')[0].files.map((x) => x.state)).toEqual(['ahead', 'behind']);
    const el = await mount(<SemesterMaterialsScreen {...props({ files: f })} />);
    const [notes, slides] = [...el.querySelectorAll<HTMLElement>('details.out-of-step li.dif-file')];
    expect(notes.querySelector('.dif-sum')!.textContent).toBe('');
    const diffBtn = diffOf(notes);
    expect(diffBtn.textContent).toBe('Diff');
    expect(notes.querySelector('.dif-diff')).toBeNull();
    await act(() => diffBtn.click());
    // The `diff` package is imported on first open: under a loaded run that takes more than a few ticks.
    for (let i = 0; i < 200 && !notes.querySelector('pre.file-diff .fd-add'); i++) await act(async () => { await new Promise((r) => setTimeout(r, 5)); });
    expect(diffBtn.getAttribute('aria-expanded')).toBe('true');
    const box = notes.querySelector('.dif-diff')!;
    expect(box.querySelector('.dif-diff-head code')!.textContent).toBe('lectures/05_trees/notes.md');
    expect(box.querySelector('.dif-diff-head .footnote')!.textContent).toBe('as released → the semester’s copy');
    // Compare on GitHub opens at the file's own diff: GitHub's anchor, the path's sha256.
    const anchor = createHash('sha256').update('lectures/05_trees/notes.md').digest('hex');
    expect(box.querySelector<HTMLAnchorElement>('a.textlink')!.getAttribute('href')).toBe(`https://github.com/${ORG}/materials/compare/upstream...main#diff-${anchor}`);
    expect([...box.querySelectorAll('pre.file-diff .fd-add')].map((x) => x.textContent!.trim())).toEqual(['+B', '+d']);
    expect(notes.querySelector('.dif-sum')!.textContent).toBe('+2 −1');
    // Diff again folds it; the counts stay.
    await act(() => diffBtn.click());
    expect(notes.querySelector('.dif-diff')).toBeNull();
    expect(notes.querySelector('.dif-sum')!.textContent).toBe('+2 −1');
    // A binary file: no line diff, Diff greyed with why.
    expect(slides.querySelector('.dif-sum')!.textContent).toBe('binary');
    const off = diffOf(slides);
    expect([off.disabled, off.title]).toEqual([true, 'A binary file: no line diff. Open it to compare.']);
  });

  it('words what a behind file’s diff compares, with no Compare on GitHub', async () => {
    const f = stepFiles() as StaticFiles;
    f.put(COURSE_ORG, 'course-materials-f2026', 'labs/lab-1/lab.ipynb', undefined, '{"x": 2}\n');
    f.put(ORG, 'materials', 'labs/lab-1/lab.ipynb', undefined, '{"x": 1}\n');
    const el = await mount(<SemesterMaterialsScreen {...props({ files: f })} />);
    const lab = [...el.querySelectorAll<HTMLElement>('details.out-of-step li.dif-file')][1];
    await act(() => diffOf(lab).click());
    for (let i = 0; i < 6; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(lab.querySelector('.dif-diff-head .footnote')!.textContent).toBe('as released → the course’s copy now');
    expect(lab.querySelector('.dif-diff-head a')).toBeNull();
  });

  it('re-releases one file, with Overwrite in its Options, and opens a PR into source for one file or every semester edit', async () => {
    const open = vi.fn();
    root = document.createElement('div');
    document.body.appendChild(root);
    const env = { user: { login: 'prof', id: 1, name: 'P', email: null, avatar_url: '' }, client: new GitHubClient({ token: () => 't', fetch: new FakeGitHub().fetch }), ops: { open } } as unknown as Env;
    const draw = (f: Files, loaded = RELEASED) => act(() => render(<EnvCtx.Provider value={env}><SemesterMaterialsScreen {...props({ files: f, loaded })} /></EnvCtx.Provider>, root!));
    // notes.md ahead, extra.md only in the semester, lab.ipynb behind, new.md added in the course.
    await draw(stepFiles({}, ['lectures/05_trees/extra.md']));
    const panel = root.querySelector<HTMLElement>('details.out-of-step')!;
    const again = (name: string) => [...panel.querySelectorAll<HTMLElement>('li.dif-file')].find((li) => li.querySelector('.dif-name')!.textContent === name)!
      .querySelector<HTMLButtonElement>('.dif-right > button.btn')!;
    expect(again('lab.ipynb').textContent).toBe('Re-release');
    expect(again('lab.ipynb').disabled).toBe(false);
    expect(again('new.md').disabled).toBe(false);
    expect([again('notes.md').disabled, again('notes.md').title]).toEqual([true, 'The semester already has the course version; re-releasing changes nothing.']);
    expect([again('extra.md').disabled, again('extra.md').title]).toEqual([true, 'Only in the semester: nothing in the course to release.']);
    await act(() => again('lab.ipynb').click());
    // Exactly that file: the course path from the release, onto the released copy.
    expect(open.mock.calls[0][0]).toMatchObject({ op: 'release.adhoc', args: { course_source_repo: 'course-materials-f2026', course_source_path: 'labs/lab-1/lab.ipynb', semester_dest_repo: 'materials', semester_dest_path: 'labs/lab-1/lab.ipynb' } });
    // Its Options: the advanced Overwrite, off, so a plain Re-release merges.
    expect(open.mock.calls[0][0].args.replace).toBeUndefined();
    expect(open.mock.calls[0][0].options).toEqual({
      replace: { tier: 'advanced', widget: 'checkbox', default: false, label: 'Overwrite the semester’s copy, discarding its edits', reason: 'A conflict then cannot happen; the semester’s edit to this file is lost.' },
    });
    // PR into source per file, beside Re-release: for a file edited in the semester (added there included), not for one edited in the course.
    const prFile = (name: string) => [...[...panel.querySelectorAll<HTMLElement>('li.dif-file')].find((li) => li.querySelector('.dif-name')!.textContent === name)!
      .querySelectorAll<HTMLButtonElement>('.dif-right > button.btn')].find((b) => b.textContent === 'PR into source')!;
    expect(prFile('notes.md').disabled).toBe(false);
    expect(prFile('extra.md').disabled).toBe(false);
    expect([prFile('lab.ipynb').disabled, prFile('lab.ipynb').title]).toEqual([true, 'Nothing edited in the semester for this file.']);
    expect([prFile('new.md').disabled, prFile('new.md').title]).toEqual([true, 'Nothing edited in the semester for this file.']);
    await act(() => prFile('notes.md').click());
    // That one path, in the semester's default materials repo: no repo prefix.
    expect(open.mock.calls[1][0]).toMatchObject({
      op: 'release.propagate_back', name: 'PR into source', title: 'lectures/05_trees/notes.md', args: { paths: 'lectures/05_trees/notes.md' },
      intro: 'Opens a pull request carrying this file’s semester edit back into the course repo, so next semester starts from it.',
    });
    // One panel-level PR into source (all), beside the filter, with its ?: the full run.
    const pr = panel.querySelector<HTMLButtonElement>('.dif-top .dif-pr > button')!;
    expect(pr.textContent).toBe('PR into source (all)');
    expect(panel.querySelector('.dif-pr .hint-pop')!.textContent).toMatch(/^Opens one pull request per course repo carrying every edit listed under Edited in semester and Both/);
    await act(() => pr.click());
    expect(open.mock.calls[2][0]).toMatchObject({ op: 'release.propagate_back', name: 'Keep for future semesters', args: {} });
    // Nothing edited in the semester (only the lab released: changed and added in the course): PR into source greyed.
    await draw(stepFiles({}, [], [], PATHS.filter((x) => !x.startsWith('lectures/'))), statusWith([rel('lab-1', 'labs/lab-1')]));
    expect([pr.disabled, pr.title]).toEqual([true, 'Nothing edited in the semester.']);
  });

  it('names the repo in a per-file PR into source outside the semester’s default materials repo', async () => {
    const open = vi.fn();
    root = document.createElement('div');
    document.body.appendChild(root);
    const env = { user: { login: 'prof', id: 1, name: 'P', email: null, avatar_url: '' }, client: new GitHubClient({ token: () => 't', fetch: new FakeGitHub().fetch }), ops: { open } } as unknown as Env;
    const other: Release = { ...rel('s5', 'lectures/05_trees'), dest: { repo: 'extras', path: 'lectures/05_trees' } };
    const base = stepFiles();
    // Only `extras` has the trees: the default `materials` has nothing released.
    const f = Object.assign(Object.create(base), { tree: (o: string, r: string, ref?: string) => base.tree(o, r === 'extras' ? 'materials' : r === 'materials' ? 'none' : r, ref) });
    await act(() => render(<EnvCtx.Provider value={env}><SemesterMaterialsScreen {...props({ files: f, loaded: statusWith([other]) })} /></EnvCtx.Provider>, root!));
    const btn = [...root.querySelectorAll<HTMLButtonElement>('details.out-of-step .dif-right > button.btn')].find((b) => b.textContent === 'PR into source')!;
    await act(() => btn.click());
    expect(open.mock.calls[0][0].args).toEqual({ paths: 'extras/lectures/05_trees/notes.md' });
  });

  it('marks a file whose release waits in an open pull request from upstream, with Open PR and Overwrite in place of Re-release', async () => {
    const open = vi.fn();
    const url = `https://github.com/${ORG}/materials/pull/7`;
    const gh = new FakeGitHub().on('GET', `/repos/${ORG}/materials/pulls?head=${encodeURIComponent(`${ORG}:upstream`)}&state=open&per_page=30`, [{ number: 7, html_url: url, title: 'Release', head: { ref: 'upstream' } }]);
    root = document.createElement('div');
    document.body.appendChild(root);
    const env = { user: { login: 'prof', id: 1, name: 'P', email: null, avatar_url: '' }, client: new GitHubClient({ token: () => 't', fetch: gh.fetch }), ops: { open } } as unknown as Env;
    // notes.md edited in the semester (its upstream blob differs from main), extra.md only in the semester, lab.ipynb behind.
    await act(() => render(<EnvCtx.Provider value={env}><SemesterMaterialsScreen {...props({ files: stepFiles({}, ['lectures/05_trees/extra.md']) })} /></EnvCtx.Provider>, root!));
    for (let i = 0; i < 6; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    // Read once for the one repo listed.
    expect(gh.seen.filter((x) => x.url.includes('/pulls?'))).toHaveLength(1);
    const row = (name: string) => [...root!.querySelectorAll<HTMLElement>('details.out-of-step li.dif-file')].find((li) => li.querySelector('.dif-name')!.textContent === name)!;
    const buttons = (li: HTMLElement) => [...li.querySelectorAll<HTMLElement>('.dif-right > .btn')].map((b) => b.textContent);
    const notes = row('notes.md');
    const note = notes.querySelector<HTMLElement>('.dif-pending[role="status"]')!;
    expect(note.querySelector('span')!.textContent).toBe('Release pending in a pull request');
    expect(buttons(notes)).not.toContain('Re-release');
    expect(buttons(notes)).toContain('PR into source');
    const openPr = note.querySelector<HTMLAnchorElement>('a.btn')!;
    expect([openPr.textContent, openPr.getAttribute('href'), openPr.getAttribute('target')]).toEqual(['Open PR', url, '_blank']);
    await act(() => [...note.querySelectorAll<HTMLButtonElement>('button')].find((b) => b.textContent === 'Overwrite')!.click());
    expect(open.mock.calls[0][0]).toMatchObject({
      op: 'release.adhoc', confirm: 'Replaces the semester’s copy of this file with the course version; its edit is lost.',
      args: { course_source_path: 'lectures/05_trees/notes.md', semester_dest_path: 'lectures/05_trees/notes.md', replace: true },
    });
    // Not pending: a file the release does not touch (only in the semester), nor one edited in the course.
    expect(row('extra.md').querySelector('.dif-pending')).toBeNull();
    expect(row('lab.ipynb').querySelector('.dif-pending')).toBeNull();
    expect(buttons(row('lab.ipynb'))).toContain('Re-release');
    // The ? says so in one line.
    expect(root.querySelector('details.out-of-step summary .hint-pop')!.textContent).toContain('Release pending in a pull request: a re-release that conflicted waits there');
  });

  it('draws no pending notice without an open pull request from upstream', async () => {
    const el = await mount(<SemesterMaterialsScreen {...props({ files: stepFiles() })} />);
    expect(el.querySelector('details.out-of-step .dif-pending')).toBeNull();
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
