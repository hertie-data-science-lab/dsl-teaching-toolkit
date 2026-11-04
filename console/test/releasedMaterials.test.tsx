// @vitest-environment happy-dom
// A semester's Released materials (`?cohort=<org>#materials`): its materials repo with the tree,
// a file opening in the viewer, a released copy edited in the semester in the Dashboard's words,
// Release by hand below for those who can change the course (adding a course repo sets its
// topic), the empty states, the nav and the route that keeps it apart from the course's
// `#materials` / `#materials-<repo>`.

import { render } from 'preact';
import { render as html } from 'preact-render-to-string';
import { act } from 'preact/test-utils';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient } from '../src/github/client';
import type { Course } from '../src/model/discovery';
import type { Loaded } from '../src/model/status';
import type { Problem, Release, Status } from '../src/model/types';
import { COHORT_SCREENS, COURSE_SCREENS, RELEASED_MATERIALS, parseHash, parseSearch, screenOf } from '../src/router';
import { SemesterMaterialsScreen } from '../src/screens/SemesterMaterials';
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

  it('links no Release page from the tree rows: the edited-copy line keeps its link', async () => {
    const el = await mount(<SemesterMaterialsScreen {...props({ folder: 'materials/lectures/05_trees' })} />);
    expect(el.querySelector('.file-tree a[href^="#release-"]')).toBeNull();
    expect(el.querySelector('.r-sub a[href="#release-s5"]')!.textContent).toBe('Release page');
    expect(el.querySelector('a[href="#release-s6"]')).toBeNull();
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

  it('says a released copy was edited, in the Dashboard’s words, with its Release page', () => {
    const t = text(<SemesterMaterialsScreen {...props()} />);
    expect(t).toContain('Released copy edited : The released copy of Lecture 5 was edited in the semester: 1 file changed. Release page');
    expect(text(<SemesterMaterialsScreen {...props({ loaded: statusWith([rel('s5', 'lectures/05_trees')]) })} />)).not.toContain('Released copy edited');
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
