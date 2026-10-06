// @vitest-environment happy-dom
// New course's first step, mounted against a fake GitHub: the org appears on GitHub while the
// step is open, and its tick and Continue follow without a click. And the remembered install
// return, which goes stale after an hour.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient, decodeBase64 } from '../src/github/client';
import type { Course } from '../src/model/discovery';
import { StaticFiles } from './staticFiles';
import { NewAssignmentScreen, S1, S2, S3, S4, S5 } from '../src/screens/NewAssignment';
import { signature } from '../src/wizards/model';
import { NewCohortScreen } from '../src/screens/NewCohort';
import { NewCourseScreen } from '../src/screens/NewCourse';
import { INSTALL_RETURN_MS, rememberInstallReturn, takeInstallReturn } from '../src/wizards/drafts';
import { POLL_MS } from '../src/wizards/verify';
import { FakeGitHub, fileBody, json } from './fake';

const ORG = 'hertie-deep-learning-e2345';
let root: HTMLElement | null = null;
beforeEach(() => {
  vi.useFakeTimers();
  localStorage.clear();
});
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
  vi.useRealTimers();
});

describe('New course step 1', () => {
  it('ticks the org and enables Continue by itself once the org appears', async () => {
    localStorage.setItem('dsl-console:wizard:new-course', JSON.stringify({ course_name: 'Deep Learning', course_code: 'E2345', admins: [] }));
    let reads = 0;
    const gh = new FakeGitHub()
      .on('GET', `/orgs/${ORG}`, () => (++reads >= 2 ? json({ login: ORG, id: 42 }) : json({ message: 'Not Found' }, 404)))
      .on('GET', `/orgs/${ORG}/memberships/hertie-dsl-bot`, { state: 'active', role: 'admin' });
    const client = new GitHubClient({ token: () => 't', fetch: gh.fetch });
    const env = { client, user: { login: 'a-example', id: 1, name: null, email: null, avatar_url: '' }, files: new StaticFiles(), kind: 'classic' } as unknown as Env;
    root = document.createElement('div');
    document.body.appendChild(root);
    await act(async () => render(<EnvCtx.Provider value={env}><NewCourseScreen files={new StaticFiles()} step={1} /></EnvCtx.Provider>, root!));
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    const cont = () => [...root!.querySelectorAll<HTMLButtonElement>('button.btn')].find((b) => b.textContent === 'Continue')!;
    const firstTick = () => root!.querySelector('.org-steps .ck')!.className;
    expect(firstTick()).toContain('no');
    expect(cont().disabled).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS); });
    await act(async () => { await vi.advanceTimersByTimeAsync(100); }); // the effect and the reads settle
    expect(reads).toBe(2);
    expect(firstTick()).toContain('ok');
    expect(cont().disabled).toBe(false);
    // All pass: nothing is read again.
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS * 3); });
    expect(reads).toBe(2);
  });
});

describe('New semester step 1', () => {
  const COURSE = 'hertie-dsl-demo-course-e1234';
  const SEM = 'hertie-dsl-demo-course-s2027';
  const course: Course = { org: COURSE, name: 'Deep Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: [{ org: 'hertie-dsl-demo-course-f2026', term: 'f2026', termLabel: 'Fall 2026' }], meta: {} };
  const now = Date.parse('2026-09-30T10:00:00Z');

  async function mountSemester(mine: { state: string; role: string }) {
    const gh = new FakeGitHub()
      .on('GET', `/orgs/${SEM}`, { login: SEM, id: 7 })
      .on('GET', `/orgs/${SEM}/memberships/hertie-dsl-bot`, { state: 'pending', role: 'admin' })
      .on('GET', `/user/memberships/orgs/${SEM}`, mine)
      .on('GET', `/repos/${COURSE}/.github/contents/semesters.yml`, fileBody('semesters.yml', 'semesters:\n- hertie-dsl-demo-course-f2026\n', 'r1'))
      .on('PUT', `/repos/${COURSE}/.github/contents/semesters.yml`, { content: { sha: 'r2' }, commit: { sha: 'c2' } });
    const client = new GitHubClient({ token: () => 't', fetch: gh.fetch });
    const env = { client, user: { login: 'a-example', id: 1, name: null, email: null, avatar_url: '' }, files: new StaticFiles(), kind: 'classic', ops: { runs: { value: [] } } } as unknown as Env;
    root = document.createElement('div');
    document.body.appendChild(root);
    await act(async () => render(<EnvCtx.Provider value={env}><NewCohortScreen course={course} files={new StaticFiles()} now={now} step={1} /></EnvCtx.Provider>, root!));
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    const cont = () => [...root!.querySelectorAll<HTMLButtonElement>('button.btn')].find((b) => b.textContent === 'Continue')!;
    const press = async () => {
      await act(async () => { cont().click(); });
      await act(async () => { await vi.advanceTimersByTimeAsync(100); });
      await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    };
    return { gh, cont, press, puts: () => gh.seen.filter((x) => x.method === 'PUT') };
  }

  it('lists the org in the course registry only when its owner presses Continue', async () => {
    const { cont, press, puts } = await mountSemester({ state: 'active', role: 'admin' });
    expect(root!.textContent).toContain('Invited. The bot joins within 15 minutes.');
    // The org exists: nothing is written by itself, on the first read or on a poll.
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS * 2); });
    expect(puts()).toHaveLength(0);
    expect(cont().disabled).toBe(false);
    await press();
    expect(puts()).toHaveLength(1);
    const body = puts()[0].body as { content: string; sha: string; message: string };
    expect(body.sha).toBe('r1');
    expect(decodeBase64(body.content)).toBe(`semesters:\n- hertie-dsl-demo-course-f2026\n- ${SEM}\n`);
    expect(body.message).toBe(`registry: add semester ${SEM}, from the DSL Teaching Console`);
    expect(root!.textContent).toContain('Listed with Deep Learning.');
    // Listed; now it waits for the bot, and nothing is written again.
    expect(cont().disabled).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS * 2); });
    expect(puts()).toHaveLength(1);
  });

  it('writes nothing for an org the person does not own', async () => {
    const { press, puts } = await mountSemester({ state: 'active', role: 'member' });
    await press();
    expect(puts()).toHaveLength(0);
    expect(root!.textContent).toContain(`You must be an owner of ${SEM}.`);
  });
});

describe('the remembered install return', () => {
  it('reopens the step within the hour, and forgets it once read or when stale', () => {
    rememberInstallReturn('?course=c#new-semester-1', 1000);
    expect(takeInstallReturn(1000 + INSTALL_RETURN_MS)).toBe('?course=c#new-semester-1');
    expect(takeInstallReturn(1000)).toBeNull();
    rememberInstallReturn('#new-course-1', 1000);
    expect(takeInstallReturn(1001 + INSTALL_RETURN_MS)).toBeNull();
  });
});

describe('New assignment: import', () => {
  const COURSE = 'hertie-dsl-demo-course-e1234';
  const course: Course = { org: COURSE, name: 'Deep Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: [{ org: 'hertie-dsl-demo-course-f2026', term: 'f2026', termLabel: 'Fall 2026' }], meta: {} };
  const now = Date.parse('2026-09-30T10:00:00Z');
  const blob = (path: string) => ({ path, mode: '100644', type: 'blob', sha: `s-${path.replace(/\//g, '_')}` });

  it('lists the source ticked, solutions and tests left out, then copies the ticked files by itself once the template exists', async () => {
    const T = 'assignment-regression';
    let exists = false;
    const gh = new FakeGitHub()
      .on('GET', '/repos/prof/old-course', { name: 'old-course', default_branch: 'main' })
      .on('GET', '/repos/prof/old-course/git/trees/main?recursive=1', { sha: 't', truncated: false, tree: [...['README.md', 'data/x.csv', 'tests/test_x.py'].map(blob), { path: 'vendor', mode: '160000', type: 'commit', sha: 'c' }] })
      .on('GET', `/repos/${COURSE}/${T}`, () => (exists ? json({ name: T }) : json({ message: 'Not Found' }, 404)))
      .on('GET', `/repos/${COURSE}/${T}/branches/main`, { name: 'main', commit: { sha: 'h1', commit: { tree: { sha: 'tr1' } } } })
      .on('GET', `/repos/${COURSE}/${T}/branches/solution`, { name: 'solution', commit: { sha: 'h2', commit: { tree: { sha: 'tr2' } } } })
      .on('GET', `/repos/${COURSE}/${T}/contents/grading_config.yml?ref=solution`, fileBody('grading_config.yml', 'title: Regression\n', 'g1'))
      .on('GET', /^\/repos\/prof\/old-course\/git\/blobs\//, { content: 'eA==', encoding: 'base64' })
      .on('POST', `/repos/${COURSE}/${T}/git/blobs`, { sha: 'nb' })
      .on('POST', `/repos/${COURSE}/${T}/git/trees`, { sha: 'nt' })
      .on('POST', `/repos/${COURSE}/${T}/git/commits`, { sha: 'nc' })
      .on('PATCH', `/repos/${COURSE}/${T}/git/refs/heads/main`, { object: { sha: 'nc' } });
    const client = new GitHubClient({ token: () => 't', fetch: gh.fetch });
    const env = { client, user: { login: 'a-example', id: 1, name: null, email: null, avatar_url: '' }, files: new StaticFiles(), kind: 'classic', ops: { runs: { value: [] } } } as unknown as Env;
    const v = { name: 'Regression', start: 'repo', source_repo: 'https://github.com/prof/old-course', type: 'individual', submit_via: 'assignment_repo', formats: ['ipynb'], autograde: 'false', completion_check: 'auto', grader_pdf: false };
    localStorage.setItem(`dsl-console:wizard:new-assignment:${COURSE}`, JSON.stringify({ v, verified: {} }));
    const props = { course, loaded: { kind: 'absent' } as const, cohortStates: {}, files: new StaticFiles(), now };
    root = document.createElement('div');
    document.body.appendChild(root);
    await act(async () => render(<EnvCtx.Provider value={env}><NewAssignmentScreen {...props} step={1} /></EnvCtx.Provider>, root!));
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    const ticks = [...root.querySelectorAll<HTMLInputElement>('input.ft-tick')].map((i) => [i.getAttribute('aria-label'), i.checked]);
    expect(ticks).toEqual([['Include data/', true], ['Include data/x.csv', true], ['Include tests/', false], ['Include tests/test_x.py', false], ['Include README.md', true]]);
    expect(root.textContent).toContain('2 of 4 files ticked');
    expect(root.textContent).toContain("These files go on the template's main branch, which students receive at hand out.");
    const vendor = [...root.querySelectorAll('li')].find((li) => li.textContent?.startsWith('vendor'))!;
    expect(vendor.textContent).toContain('not copied');
    expect(vendor.querySelector('input')).toBeNull();
    expect(root.textContent).toContain('prof/old-course has no solution branch, so only its brief is copied.');

    // The template now exists (the operation ran); every step verified: the check step copies.
    exists = true;
    const verified = { 1: signature(v, S1), 2: signature(v, S2), 3: signature(v, S3), 4: signature(v, S4), 5: signature(v, S5) };
    localStorage.setItem(`dsl-console:wizard:new-assignment:${COURSE}`, JSON.stringify({ v, verified, extrasSaved: T }));
    render(null, root);
    await act(async () => render(<EnvCtx.Provider value={env}><NewAssignmentScreen {...props} step={6} /></EnvCtx.Provider>, root!));
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    expect(root.textContent).toContain('Copied 2 files to main.');
    expect([...root.querySelectorAll('button')].some((b) => b.textContent === 'Copy again')).toBe(false);
    expect(gh.seen.find((x) => x.method === 'POST' && x.url.endsWith('/git/trees'))!.body).toMatchObject({ base_tree: 'tr1', tree: [{ path: 'README.md' }, { path: 'data/x.csv' }] });
    expect(root.textContent).toContain('Created. Nothing reaches students until you add it to a schedule.');
    expect(root.textContent).toContain('Add to the Fall 2026 schedule');
    // Written by hand: nothing to derive. Derived: Derive is offered straight away.
    expect(root.textContent).not.toContain('Derive the student version now');
    const key = `dsl-console:wizard:new-assignment:${COURSE}`;
    const saved = JSON.parse(localStorage.getItem(key)!);
    const dv = { ...v, starter: 'derived' };
    localStorage.setItem(key, JSON.stringify({ ...saved, v: dv, verified: { ...saved.verified, 4: signature(dv, S4) } }));
    render(null, root);
    await act(async () => render(<EnvCtx.Provider value={env}><NewAssignmentScreen {...props} step={6} /></EnvCtx.Provider>, root!));
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    expect(root.textContent).toContain('Created.');
    expect([...root.querySelectorAll('button')].some((b) => b.textContent === 'Derive the student version now')).toBe(true);
  });

  it('never says Created while the source cannot be read: the check fails with its sentence and a way to read it again', async () => {
    const T = 'assignment-regression';
    let sourceUp = false;
    const gh = new FakeGitHub()
      .on('GET', '/repos/prof/old-course', () => (sourceUp ? json({ name: 'old-course', default_branch: 'main' }) : json({ message: 'Server Error' }, 500)))
      .on('GET', '/repos/prof/old-course/git/trees/main?recursive=1', { sha: 't', truncated: false, tree: ['README.md'].map(blob) })
      .on('GET', `/repos/${COURSE}/${T}`, { name: T })
      .on('GET', `/repos/${COURSE}/${T}/branches/main`, { name: 'main', commit: { sha: 'h1', commit: { tree: { sha: 'tr1' } } } })
      .on('GET', `/repos/${COURSE}/${T}/branches/solution`, { name: 'solution', commit: { sha: 'h2', commit: { tree: { sha: 'tr2' } } } })
      .on('GET', `/repos/${COURSE}/${T}/contents/grading_config.yml?ref=solution`, fileBody('grading_config.yml', 'title: Regression\n', 'g1'));
    const client = new GitHubClient({ token: () => 't', fetch: gh.fetch });
    const env = { client, user: { login: 'a-example', id: 1, name: null, email: null, avatar_url: '' }, files: new StaticFiles(), kind: 'classic', ops: { runs: { value: [] } } } as unknown as Env;
    const v = { name: 'Regression', start: 'repo', source_repo: 'prof/old-course', type: 'individual', submit_via: 'assignment_repo', formats: ['ipynb'], autograde: 'false', completion_check: 'auto', grader_pdf: false };
    const verified = { 1: signature(v, S1), 2: signature(v, S2), 3: signature(v, S3), 4: signature(v, S4), 5: signature(v, S5) };
    localStorage.setItem(`dsl-console:wizard:new-assignment:${COURSE}`, JSON.stringify({ v, verified, extrasSaved: T }));
    const props = { course, loaded: { kind: 'absent' } as const, cohortStates: {}, files: new StaticFiles(), now };
    root = document.createElement('div');
    document.body.appendChild(root);
    await act(async () => render(<EnvCtx.Provider value={env}><NewAssignmentScreen {...props} step={6} /></EnvCtx.Provider>, root!));
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    expect(root.textContent).toContain('prof/old-course can be read (could not tell)');
    expect(root.textContent).not.toContain('Created');
    expect(root.textContent).not.toContain(' of prof/old-course');
    expect(gh.seen.some((x) => x.method === 'POST')).toBe(false);
    const again = [...root.querySelectorAll<HTMLButtonElement>('button.btn')].find((b) => b.textContent === 'Read it again')!;
    expect(again).toBeTruthy();
    sourceUp = true;
    await act(async () => { again.click(); await vi.advanceTimersByTimeAsync(100); });
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    expect(root.textContent).not.toContain('Read it again');
    expect(root.textContent).toContain('1 files from main of prof/old-course');
  });

  it('copies main and solution by itself, and a retry copies only the branch that failed', async () => {
    const T = 'assignment-regression';
    let refusals = 1;
    const gh = new FakeGitHub()
      .on('GET', '/repos/prof/old-course', { name: 'old-course', default_branch: 'main' })
      .on('GET', '/repos/prof/old-course/git/trees/main?recursive=1', { sha: 't', truncated: false, tree: ['README.md', 'tests/test_x.py'].map(blob) })
      .on('GET', '/repos/prof/old-course/branches/solution', { name: 'solution', commit: { sha: 'ps', commit: { tree: { sha: 'pt' } } } })
      .on('GET', '/repos/prof/old-course/git/trees/solution?recursive=1', { sha: 't2', truncated: false, tree: ['answer.ipynb', 'tests/test_x.py', '.env'].map(blob) })
      .on('GET', `/repos/${COURSE}/${T}`, { name: T })
      .on('GET', `/repos/${COURSE}/${T}/branches/main`, { name: 'main', commit: { sha: 'h1', commit: { tree: { sha: 'tr1' } } } })
      .on('GET', `/repos/${COURSE}/${T}/branches/solution`, { name: 'solution', commit: { sha: 'h2', commit: { tree: { sha: 'tr2' } } } })
      .on('GET', `/repos/${COURSE}/${T}/contents/grading_config.yml?ref=solution`, fileBody('grading_config.yml', 'title: Regression\n', 'g1'))
      .on('GET', /^\/repos\/prof\/old-course\/git\/blobs\//, { content: 'eA==', encoding: 'base64' })
      .on('POST', `/repos/${COURSE}/${T}/git/blobs`, { sha: 'nb' })
      .on('POST', `/repos/${COURSE}/${T}/git/trees`, { sha: 'nt' })
      .on('POST', `/repos/${COURSE}/${T}/git/commits`, { sha: 'nc' })
      .on('PATCH', `/repos/${COURSE}/${T}/git/refs/heads/main`, { object: { sha: 'nc' } })
      .on('PATCH', `/repos/${COURSE}/${T}/git/refs/heads/solution`, () => (refusals-- > 0 ? json({ message: 'Update is not a fast forward' }, 422) : json({ object: { sha: 'nc' } })));
    const client = new GitHubClient({ token: () => 't', fetch: gh.fetch });
    const env = { client, user: { login: 'a-example', id: 1, name: null, email: null, avatar_url: '' }, files: new StaticFiles(), kind: 'classic', ops: { runs: { value: [] } } } as unknown as Env;
    const v = { name: 'Regression', start: 'repo', source_repo: 'prof/old-course', type: 'individual', submit_via: 'assignment_repo', formats: ['ipynb'], autograde: 'false', completion_check: 'auto', grader_pdf: false };
    const verified = { 1: signature(v, S1), 2: signature(v, S2), 3: signature(v, S3), 4: signature(v, S4), 5: signature(v, S5) };
    localStorage.setItem(`dsl-console:wizard:new-assignment:${COURSE}`, JSON.stringify({ v, verified, extrasSaved: T }));
    const props = { course, loaded: { kind: 'absent' } as const, cohortStates: {}, files: new StaticFiles(), now };
    root = document.createElement('div');
    document.body.appendChild(root);
    await act(async () => render(<EnvCtx.Provider value={env}><NewAssignmentScreen {...props} step={6} /></EnvCtx.Provider>, root!));
    for (let i = 0; i < 3; i++) await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    const trees = () => gh.seen.filter((x) => x.method === 'POST' && x.url.endsWith('/git/trees')).map((x) => (x.body as { base_tree: string }).base_tree);
    expect(trees()).toEqual(['tr1', 'tr2']);
    expect(gh.seen.find((x) => x.method === 'POST' && x.url.endsWith('/git/trees') && (x.body as { base_tree: string }).base_tree === 'tr2')!.body).toMatchObject({ tree: [{ path: 'answer.ipynb' }, { path: 'tests/test_x.py' }] });
    expect(root.textContent).toContain('Copied 1 file to main.');
    expect(root.textContent).toContain('Copy again to try only what is missing.');
    expect(root.textContent).not.toContain('Created.');
    const again = [...root.querySelectorAll<HTMLButtonElement>('button.btn')].find((b) => b.textContent === 'Copy again')!;
    expect(again).toBeTruthy();
    await act(async () => { again.click(); await vi.advanceTimersByTimeAsync(100); });
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    expect(trees()).toEqual(['tr1', 'tr2', 'tr2']);
    expect(root.textContent).toContain('Copied 2 files to solution.');
    expect(root.textContent).toContain('Created. Nothing reaches students until you add it to a schedule.');
  });
});
