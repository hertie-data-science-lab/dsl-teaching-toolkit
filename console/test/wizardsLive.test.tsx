// @vitest-environment happy-dom
// New course's first step, mounted against a fake GitHub: the org appears on GitHub while the
// step is open, and its tick and Continue follow without a click. And the remembered install
// return, which goes stale after an hour.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient } from '../src/github/client';
import { StaticFiles } from '../src/model/files';
import { NewCourseScreen } from '../src/screens/NewCourse';
import { INSTALL_RETURN_MS, rememberInstallReturn, takeInstallReturn } from '../src/wizards/drafts';
import { POLL_MS } from '../src/wizards/verify';
import { FakeGitHub, json } from './fake';

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

describe('the remembered install return', () => {
  it('reopens the step within the hour, and forgets it once read or when stale', () => {
    rememberInstallReturn('?course=c#new-semester-1', 1000);
    expect(takeInstallReturn(1000 + INSTALL_RETURN_MS)).toBe('?course=c#new-semester-1');
    expect(takeInstallReturn(1000)).toBeNull();
    rememberInstallReturn('#new-course-1', 1000);
    expect(takeInstallReturn(1001 + INSTALL_RETURN_MS)).toBeNull();
  });
});
