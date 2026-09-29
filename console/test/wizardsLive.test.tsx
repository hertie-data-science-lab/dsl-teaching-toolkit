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
  it('lists the new org in the course registry once it exists, and says the bot joins within 15 minutes', async () => {
    const COURSE = 'hertie-dsl-demo-course-e1234';
    const SEM = 'hertie-dsl-demo-course-s2027';
    const course: Course = { org: COURSE, name: 'Deep Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: [{ org: 'hertie-dsl-demo-course-f2026', term: 'f2026', termLabel: 'Fall 2026' }], meta: {} };
    const gh = new FakeGitHub()
      .on('GET', `/orgs/${SEM}`, { login: SEM, id: 7 })
      .on('GET', `/orgs/${SEM}/memberships/hertie-dsl-bot`, { state: 'pending', role: 'admin' })
      .on('GET', `/repos/${COURSE}/.github/contents/semesters.yml`, fileBody('semesters.yml', 'semesters:\n- hertie-dsl-demo-course-f2026\n', 'r1'))
      .on('PUT', `/repos/${COURSE}/.github/contents/semesters.yml`, { content: { sha: 'r2' }, commit: { sha: 'c2' } });
    const client = new GitHubClient({ token: () => 't', fetch: gh.fetch });
    const env = { client, user: { login: 'a-example', id: 1, name: null, email: null, avatar_url: '' }, files: new StaticFiles(), kind: 'classic', ops: { runs: { value: [] } } } as unknown as Env;
    root = document.createElement('div');
    document.body.appendChild(root);
    const now = Date.parse('2026-09-30T10:00:00Z');
    await act(async () => render(<EnvCtx.Provider value={env}><NewCohortScreen course={course} files={new StaticFiles()} now={now} step={1} /></EnvCtx.Provider>, root!));
    await act(async () => { await vi.advanceTimersByTimeAsync(100); });
    await act(async () => { await vi.advanceTimersByTimeAsync(100); }); // the registry read, then its write
    const puts = gh.seen.filter((x) => x.method === 'PUT');
    expect(puts).toHaveLength(1);
    const body = puts[0].body as { content: string; sha: string; message: string };
    expect(body.sha).toBe('r1');
    expect(decodeBase64(body.content)).toBe(`semesters:\n- hertie-dsl-demo-course-f2026\n- ${SEM}\n`);
    expect(body.message).toBe(`registry: add semester ${SEM}, from the DSL Teaching Console`);
    expect(root!.textContent).toContain('Invited. The bot joins within 15 minutes.');
    // Polling on does not write it again.
    await act(async () => { await vi.advanceTimersByTimeAsync(POLL_MS * 2); });
    expect(gh.seen.filter((x) => x.method === 'PUT')).toHaveLength(1);
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
