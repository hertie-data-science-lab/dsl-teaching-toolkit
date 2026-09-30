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
import { StaticFiles } from '../src/model/files';
import { NewAssignmentScreen, S1, S2, S3, S4 } from '../src/screens/NewAssignment';
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

  it('lists the source ticked, solutions and tests left out, then copies the ticked files once the template exists', async () => {
    const T = 'assignment-regression';
    let exists = false;
    const gh = new FakeGitHub()
      .on('GET', '/repos/prof/old-course', { name: 'old-course', default_branch: 'main' })
      .on('GET', '/repos/prof/old-course/git/trees/main?recursive=1', { sha: 't', truncated: false, tree: ['README.md', 'data/x.csv', 'tests/test_x.py'].map(blob) })
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
    expect(root.textContent).toContain('2 of 3 files ticked');
    expect(root.textContent).toContain('prof/old-course has no solution branch, so only its brief is copied.');

    // The template now exists (the operation ran); every step verified: the check step copies.
    exists = true;
    const verified = { 1: signature(v, S1), 2: signature(v, S2), 3: signature(v, S3), 4: signature(v, S4) };
    localStorage.setItem(`dsl-console:wizard:new-assignment:${COURSE}`, JSON.stringify({ v, verified, extrasSaved: T }));
    render(null, root);
    await act(async () => render(<EnvCtx.Provider value={env}><NewAssignmentScreen {...props} step={5} /></EnvCtx.Provider>, root!));
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    const copy = [...root.querySelectorAll<HTMLButtonElement>('button.btn')].find((b) => b.textContent === 'Copy the files')!;
    expect(copy).toBeTruthy();
    await act(async () => { copy.click(); await vi.advanceTimersByTimeAsync(100); });
    expect(root.textContent).toContain('Copied 2 files to main.');
    expect(gh.seen.find((x) => x.method === 'POST' && x.url.endsWith('/git/trees'))!.body).toMatchObject({ base_tree: 'tr1', tree: [{ path: 'README.md' }, { path: 'data/x.csv' }] });
    expect(root.textContent).toContain('Created. Nothing reaches students until you add it to a schedule.');
    expect(root.textContent).toContain('Add to the Fall 2026 schedule');
  });
});
