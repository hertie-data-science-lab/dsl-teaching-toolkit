// @vitest-environment happy-dom
// The Releases settings (plan 2026-11-04, decision 7), clicked through against a fake GitHub:
// Course details' Releases sub-section (Release from = the `dsl-materials` topic, Release into =
// `release_destinations`, Default destination = `materials_repo`, all in course.yml's
// `defaults:`) and the Semester defaults Releases fold (the same into semester.yml).

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, describe, expect, it } from 'vitest';
import { parse } from 'yaml';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient, decodeBase64 } from '../src/github/client';
import type { Course } from '../src/model/discovery';
import { StatusStore, type Loaded } from '../src/model/status';
import type { Status } from '../src/model/types';
import { DispatchAdapter } from '../src/ops/adapter';
import { OpsSession } from '../src/ops/session';
import { DetailsScreen } from '../src/screens/CourseEdit';
import { SemesterDefaultsScreen } from '../src/screens/SemesterSettings';
import { destinationOptions } from '../src/tiers/course';
import { inheritedInto } from '../src/ui/ReleaseChips';
import { StaticFiles } from './staticFiles';
import example from './fixtures/status.example.json';
import { FakeGitHub, json, type Seen } from './fake';

const COURSE_ORG = 'hertie-dsl-demo-course-e1234';
const ORG = 'hertie-dsl-demo-f2026';
const USER = { login: 'a-example', id: 1, name: 'A. Example', email: null, avatar_url: '' };
const cohort = { org: ORG, term: 'f2026', termLabel: 'Fall 2026' };
const course: Course = { org: COURSE_ORG, name: 'Machine Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: [cohort], meta: {} };
const STATUS = example as unknown as Status;
const loaded: Loaded = { kind: 'ready', status: STATUS, sha: 's', stale: [] };
const COURSE_FILE = `${COURSE_ORG}/.github/course.yml`;
const META = 'course:\n  name: Machine Learning\n  code: E1234\n';

let root: HTMLElement | null = null;
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
});

function setup(f: StaticFiles, topics: string[] = ['dsl-materials']) {
  const gh = new FakeGitHub()
    .on('PUT', /\/contents\//, () => json({ content: { sha: 'b1' }, commit: { sha: 'c1' } }))
    .on('GET', /\/topics$/, { names: topics })
    .on('PUT', /\/topics$/, { names: [] })
    .on('GET', /\/check-runs/, { check_runs: [] });
  const client = new GitHubClient({ token: () => 't', fetch: gh.fetch });
  const env: Env = { client, user: USER, ops: new OpsSession(new DispatchAdapter(client, () => USER.login)), statuses: new StatusStore(client), files: f, pollMs: 0 };
  return { gh, env };
}
async function mount(env: Env, view: preact.VNode) {
  root = document.body.appendChild(document.createElement('div'));
  await act(() => render(<EnvCtx.Provider value={env}>{view}</EnvCtx.Provider>, root!));
}
const q = <T extends Element>(sel: string) => root!.querySelector<T>(sel)!;
const button = (label: string, within: Element = root!) => [...within.querySelectorAll('button')].find((b) => b.textContent?.trim() === label || b.getAttribute('aria-label') === label)!;
async function click(el: HTMLElement) {
  await act(() => el.click());
}
async function type(sel: string, value: string, ev = 'input') {
  await act(() => {
    const el = q<HTMLInputElement>(sel);
    el.value = value;
    el.dispatchEvent(new Event(ev, { bubbles: true }));
  });
}
async function settle(until: () => boolean) {
  for (let i = 0; i < 200 && !until(); i++) await act(() => new Promise((r) => setTimeout(r, 0)));
}
const puts = (gh: FakeGitHub, path: string) => gh.seen.filter((s) => s.method === 'PUT' && s.url.endsWith(`/contents/${path}`));
const topicPuts = (gh: FakeGitHub) => gh.seen.filter((s) => s.method === 'PUT' && s.url.endsWith('/topics'));
const body = (s: Seen) => decodeBase64((s.body as { content: string }).content);
/** The chips of one list: [name, removable]. */
const chips = (id: string) => [...q(`#${id}`).querySelectorAll('.chip')].map((c) => [c.firstChild!.textContent!.trim(), !!c.querySelector('button.x')]);
async function addDestination(listId: string, name: string) {
  await click(button('Add…', q(`#${listId}`)));
  await type('#rp-new-name', name);
  await click(button('Choose', q('[role="dialog"]')));
}
async function save(gh: FakeGitHub, path: string) {
  await click(button('Save'));
  await settle(() => puts(gh, path).length > 0);
  return body(puts(gh, path)[0]);
}

describe('Course details: Releases', () => {
  const courseFiles = (text = META) => new StaticFiles({ [COURSE_FILE]: text }, {}, {}, {
    [COURSE_ORG]: [{ name: 'course-materials-f2026', topics: ['dsl-materials'] }, { name: 'lecture-slides' }, { name: 'assignment-3-f2026', topics: ['dsl-template'] }],
    [ORG]: [{ name: 'materials' }, { name: 'lecture-code' }, { name: 'semester-config' }, { name: 'assignment-2-anna' }],
  });
  const details = (f: StaticFiles) => <DetailsScreen course={course} loaded={loaded} cohortStates={{ [ORG]: loaded }} files={f} now={0} />;

  it('draws Release from, Release into and Default destination; the institution’s chip is grey and not removable', async () => {
    const f = courseFiles();
    await mount(setup(f).env, details(f));
    const section = q('.rel-section');
    expect(section.querySelector('h3')!.textContent).toMatch(/^Releases/);
    expect(chips('cd-from')).toEqual([['course-materials-f2026', true]]);
    expect(chips('cd-into')).toEqual([['materials', false]]);
    expect(q('#cd-into .chip.fixed').textContent).toContain('· from the institution');
    const sel = q<HTMLSelectElement>('#cdx-materials_repo');
    expect(sel.options[0].textContent).toBe('materials, from the institution (default)');
    expect(sel.value).toBe('');
    // Out of the generic defaults form: asked once, under Releases.
    expect(root!.querySelectorAll('[id="cdx-materials_repo"]')).toHaveLength(1);
  });

  it('adds a destination and saves it into course.yml’s defaults:, the skeleton line uncommented', async () => {
    const f = courseFiles();
    const { gh, env } = setup(f);
    await mount(env, details(f));
    await addDestination('cd-into', 'lecture-code');
    expect(chips('cd-into')).toEqual([['materials', false], ['lecture-code', true]]);
    const text = await save(gh, 'course.yml');
    expect(parse(text).defaults).toEqual({ release_destinations: ['lecture-code'] });
    expect(text).toContain('\n  release_destinations: [lecture-code]\n');
  });

  it('refuses a reserved name in the picker, and offers the semester’s eligible repos', async () => {
    const f = courseFiles();
    await mount(setup(f).env, details(f));
    await click(button('Add…', q('#cd-into')));
    const rows = [...q('[role="dialog"]').querySelectorAll('.rp-name')].map((r) => r.textContent);
    expect(rows).toEqual(['lecture-code']);
    await type('#rp-new-name', 'semester-config');
    await click(button('Choose', q('[role="dialog"]')));
    expect(q('[role="dialog"]').textContent).toContain('That name is reserved for the semester’s config.');
  });

  it('removes the course’s own destination, and the key goes back to its comment', async () => {
    const f = courseFiles(`${META}defaults:\n  release_destinations: [lecture-code, extra]\n`);
    const { gh, env } = setup(f);
    await mount(env, details(f));
    await click(button('Remove extra'));
    expect(parse(await save(gh, 'course.yml')).defaults).toEqual({ release_destinations: ['lecture-code'] });
  });

  it('writes the default destination chosen from the Release into list', async () => {
    const f = courseFiles(`${META}defaults:\n  release_destinations: [lecture-code]\n`);
    const { gh, env } = setup(f);
    await mount(env, details(f));
    const sel = q<HTMLSelectElement>('#cdx-materials_repo');
    expect([...sel.options].map((o) => o.value)).toEqual(['', 'lecture-code']);
    await type('#cdx-materials_repo', 'lecture-code', 'change');
    expect(parse(await save(gh, 'course.yml')).defaults).toEqual({ release_destinations: ['lecture-code'], materials_repo: 'lecture-code' });
  });

  it('Release from: × unsets the dsl-materials topic, Add a repo… sets it on an Other repo', async () => {
    const f = courseFiles();
    const { gh, env } = setup(f, ['dsl-materials', 'keep-me']);
    await mount(env, details(f));
    await click(button('Remove course-materials-f2026'));
    await settle(() => topicPuts(gh).length > 0);
    expect(topicPuts(gh)[0].url).toContain(`/repos/${COURSE_ORG}/course-materials-f2026/topics`);
    expect(topicPuts(gh)[0].body).toEqual({ names: ['keep-me'] });
    await settle(() => chips('cd-from').length === 0);
    await click(button('Add a repo…', q('#cd-from')));
    const rows = [...q('[role="dialog"]').querySelectorAll('.rp-name')].map((r) => r.textContent);
    expect(rows).toEqual(['course-materials-f2026', 'lecture-slides']);
    await click(q<HTMLInputElement>('[role="dialog"] input[value="lecture-slides"]'));
    await click(button('Add', q('[role="dialog"]')));
    await settle(() => topicPuts(gh).length > 1);
    expect(topicPuts(gh)[1].url).toContain('/lecture-slides/topics');
    expect(topicPuts(gh)[1].body).toEqual({ names: ['dsl-materials', 'keep-me'] });
    // No course.yml write: the topic is the repo's.
    expect(puts(gh, 'course.yml')).toHaveLength(0);
  });

  it('a read-only viewer sees the chips without × or Add', async () => {
    const f = courseFiles();
    await mount(setup(f).env, <DetailsScreen course={{ ...course, write: false }} loaded={loaded} cohortStates={{}} files={f} now={0} />);
    expect(chips('cd-from')).toEqual([['course-materials-f2026', false]]);
    expect(q('#cd-into').querySelector('button')).toBeNull();
  });
});

describe('Semester defaults: the Releases fold', () => {
  // The course adds lecture-code to the institution's list (status.json, merged).
  const status: Status = {
    ...STATUS,
    course: { ...STATUS.course!, defaults: { ...STATUS.course!.defaults, release_destinations: { value: ['lecture-code'], source: 'course' } } },
    semester: { ...STATUS.semester!, defaults: { ...STATUS.semester!.defaults, release_destinations: { value: ['lecture-code'], source: 'course' } } },
  };
  const semLoaded: Loaded = { kind: 'ready', status, sha: 's', stale: [] };
  const SEMESTER = '# INSTRUCTOR-OWNED\ndefaults:\n  late_window_days: 5\n';
  const semFiles = (text = SEMESTER) => new StaticFiles({ [`${ORG}/semester-config/semester.yml`]: text, [`${ORG}/semester-config/schedule.yml`]: 'semester:\n  start: {type: start, event_datetime: 2026-09-07}\n' }, {}, {}, {
    [ORG]: [{ name: 'materials' }, { name: 'lecture-code' }, { name: 'lab-data' }, { name: 'grades-anna' }],
  });
  const page = (f: StaticFiles) => <SemesterDefaultsScreen course={course} cohort={cohort} loaded={semLoaded} files={f} now={0} />;

  it('greys what the course gives, with its source, and none of it is removable', async () => {
    const f = semFiles();
    await mount(setup(f).env, page(f));
    expect(chips('sr-into')).toEqual([['materials', false], ['lecture-code', false]]);
    expect([...q('#sr-into').querySelectorAll('.chip.fixed .sub')].map((s) => s.textContent)).toEqual(['· from the institution', '· from the course']);
    expect(q('#sr-into-label').textContent).toContain('this course’s default: materials, lecture-code');
    expect(q<HTMLSelectElement>('#sr-materials_repo').options[0].textContent).toBe('materials, from the institution (default)');
  });

  it('adds the semester’s own destination and writes it with the default destination into semester.yml', async () => {
    const f = semFiles();
    const { gh, env } = setup(f);
    await mount(env, page(f));
    await click(button('Add…', q('#sr-into')));
    // The picker offers the semester's repos not listed yet; marks repos are refused.
    expect([...q('[role="dialog"]').querySelectorAll('.rp-name')].map((r) => r.textContent)).toEqual(['lab-data']);
    await click(button('Choose', q('[role="dialog"]')));
    expect(chips('sr-into')).toEqual([['materials', false], ['lecture-code', false], ['lab-data', true]]);
    await type('#sr-materials_repo', 'lab-data', 'change');
    const text = await save(gh, 'semester.yml');
    expect(parse(text).defaults).toEqual({ late_window_days: 5, release_destinations: ['lab-data'], materials_repo: 'lab-data' });
  });

  it('removes the semester’s own destination', async () => {
    const f = semFiles(`${SEMESTER}  release_destinations: [lab-data, extra]\n`);
    const { gh, env } = setup(f);
    await mount(env, page(f));
    await click(button('Remove lab-data'));
    expect(parse(await save(gh, 'semester.yml')).defaults).toEqual({ late_window_days: 5, release_destinations: ['extra'] });
  });
});

describe('the pieces', () => {
  it('names each inherited destination by who set it, the default destination first, once', () => {
    expect(inheritedInto({ value: 'lecture-code', source: 'course' }, { value: ['lecture-code', 'labs'], source: 'course' }, 'the course'))
      .toEqual([{ name: 'lecture-code', from: 'the course' }, { name: 'labs', from: 'the course' }]);
    // status.json's per-item source, where it gives one.
    expect(inheritedInto({ value: 'materials', source: 'institution' }, { value: ['materials', 'labs'], source: 'semester', items: [{ value: 'materials', source: 'institution' }, { value: 'labs', source: 'semester' }] }, 'the course'))
      .toEqual([{ name: 'materials', from: 'the institution' }, { name: 'labs', from: 'this semester' }]);
  });

  it('lists the inherited default first, then the rest, and keeps a saved value the list lacks', () => {
    expect(destinationOptions({ value: 'materials', source: 'course' }, ['materials', 'lab'], 'old')).toEqual([
      { value: '', label: 'materials, from the course (default)' }, { value: 'lab', label: 'lab' }, { value: 'old', label: 'old' },
    ]);
    expect(destinationOptions({ value: 'materials', source: 'institution' }, ['materials'], 'materials').map((o) => o.label)).toEqual(['materials, from the institution (default)', 'materials (set here)']);
  });
});
