// @vitest-environment happy-dom
// Pending org invitations (finding 42): discovery lists them for course and semester orgs with
// the role when it can be told; a classic token accepts in the console and discovery runs again;
// a refused accept, the GitHub App and a fine-grained token go to GitHub's accept page; nothing
// pending shows no card.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, describe, expect, it } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient } from '../src/github/client';
import { discoverEstate, invitationUrl, type Invitation, type TokenKind } from '../src/model/discovery';
import { Invitations } from '../src/screens/Home';
import { FakeGitHub, fileBody, json } from './fake';

const LOGIN = 'new-ta';
const COURSE = 'hertie-dsl-demo-course';
const SEM = 'hertie-dsl-demo-f2026';
const client = (fake: FakeGitHub) => new GitHubClient({ token: () => 't', fetch: fake.fetch });

function estateFake(pending: { org: string; role: string }[]) {
  return new FakeGitHub()
    .on('GET', '/user/orgs?per_page=100&page=1', [])
    .on('GET', '/user/memberships/orgs?state=active&per_page=100&page=1', [])
    .on('GET', '/user/memberships/orgs?state=pending&per_page=100&page=1', pending.map((p) => ({ state: 'pending', role: p.role, organization: { login: p.org } })))
    .on('GET', `/repos/${COURSE}/.github`, { name: '.github', topics: ['dsl-course-hub'], permissions: { push: false, pull: true } })
    .on('GET', `/repos/${COURSE}/.github/contents/dsl-course.yml`, fileBody('dsl-course.yml', `course_name: Deep Learning\npeople:\n  course_admins:\n    - github_handle: New-TA\n`))
    .on('GET', `/repos/${SEM}/.github`, { name: '.github', topics: ['dsl-semester'], permissions: { push: false, pull: true } })
    .on('GET', `/repos/${SEM}/semester-config/contents/.system/dsl-course.yml`, fileBody('dsl-course.yml', `course: ${COURSE}\n`))
    .on('GET', '/repos/other-org/.github', { name: '.github', topics: [], permissions: { push: false, pull: true } });
}

describe('listing pending invitations', () => {
  it('names course and semester orgs with the role when it can be told, and skips other orgs', async () => {
    const fake = estateFake([{ org: COURSE, role: 'member' }, { org: SEM, role: 'member' }, { org: 'other-org', role: 'member' }]);
    const e = await discoverEstate(client(fake), { kind: 'app', login: LOGIN });
    expect(e.invited).toEqual([
      { org: COURSE, name: 'Deep Learning', role: 'course admin' },
      { org: SEM, name: 'Deep Learning, Fall 2026', role: null },
    ]);
    expect(e.courses).toEqual([]);
  });

  it('takes an owner invitation to a semester for an instructor', async () => {
    const e = await discoverEstate(client(estateFake([{ org: SEM, role: 'admin' }])), { kind: 'classic', login: LOGIN });
    expect(e.invited).toEqual([{ org: SEM, name: 'Deep Learning, Fall 2026', role: 'instructor' }]);
  });

  it('finds none when nothing is pending', async () => {
    const e = await discoverEstate(client(estateFake([])), { kind: 'classic', login: LOGIN });
    expect(e.invited).toEqual([]);
  });
});

// ------------------------------------------------------------------------ the card

const INVITES: Invitation[] = [
  { org: COURSE, name: 'Deep Learning', role: 'instructor' },
  { org: SEM, name: 'Deep Learning, Fall 2026', role: null },
];

let host: HTMLElement | null = null;
afterEach(() => {
  if (host) render(null, host);
  host?.remove();
  host = null;
});

function mount(invited: Invitation[], kind: TokenKind, fake: FakeGitHub) {
  const calls = { rediscover: 0 };
  const env = { client: client(fake), user: { login: LOGIN, id: 1, name: null, email: null, avatar_url: '' }, rediscover: async () => void calls.rediscover++ } as unknown as Env;
  host = document.createElement('div');
  document.body.appendChild(host);
  act(() => render(<EnvCtx.Provider value={env}><Invitations invited={invited} kind={kind} /></EnvCtx.Provider>, host!));
  return { host, calls };
}

const flush = () => act(async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); await new Promise((r) => setTimeout(r, 0)); });
const buttons = (h: HTMLElement) => [...h.querySelectorAll('button')];
const links = (h: HTMLElement) => [...h.querySelectorAll('a')].map((a) => a.getAttribute('href'));

describe('the invitations card', () => {
  it('lists every invitation, with the role when known', () => {
    const { host } = mount(INVITES, 'classic', new FakeGitHub());
    const text = host.textContent ?? '';
    expect(host.querySelector('h2')?.textContent).toBe('Invitations');
    expect(text).toContain('You have been invited to Deep Learning as an instructor.');
    expect(text).toContain('You have been invited to Deep Learning, Fall 2026.');
    expect(buttons(host).map((b) => b.textContent)).toEqual(['Accept', 'Accept']);
  });

  it('accepts with a classic token, then reads the courses again', async () => {
    const fake = new FakeGitHub().on('PATCH', `/user/memberships/orgs/${COURSE}`, { state: 'active', role: 'member', organization: { login: COURSE } });
    const { host, calls } = mount(INVITES, 'classic', fake);
    await act(async () => buttons(host)[0].click());
    await flush();
    const patch = fake.seen.find((r) => r.method === 'PATCH')!;
    expect(patch.url).toBe(`https://api.github.com/user/memberships/orgs/${COURSE}`);
    expect(patch.body).toEqual({ state: 'active' });
    expect(calls.rediscover).toBe(1);
    expect(host.textContent).not.toContain('invited to Deep Learning as');
    expect(host.textContent).toContain('Deep Learning, Fall 2026');
  });

  it('falls back to GitHub’s accept page when GitHub refuses', async () => {
    const fake = new FakeGitHub().on('PATCH', `/user/memberships/orgs/${COURSE}`, () => json({ message: 'Resource not accessible by integration' }, 403));
    const { host, calls } = mount(INVITES.slice(0, 1), 'classic', fake);
    await act(async () => buttons(host)[0].click());
    await flush();
    expect(calls.rediscover).toBe(0);
    expect(buttons(host)).toEqual([]);
    expect(links(host)).toEqual([invitationUrl(COURSE)]);
    expect(host.textContent).toContain('GitHub did not let the console accept it; accept it on GitHub instead.');
  });

  it('sends the GitHub App to GitHub, and reads again when the person comes back', async () => {
    const fake = new FakeGitHub();
    const { host, calls } = mount(INVITES, 'app', fake);
    expect(buttons(host)).toEqual([]);
    expect(links(host)).toEqual([invitationUrl(COURSE), invitationUrl(SEM)]);
    window.dispatchEvent(new Event('focus'));
    expect(calls.rediscover).toBe(0); // not away yet
    const a = host.querySelector('a')!;
    a.addEventListener('click', (e) => e.preventDefault());
    await act(async () => a.click());
    window.dispatchEvent(new Event('focus'));
    await flush();
    expect(calls.rediscover).toBe(1);
    expect(fake.seen.filter((r) => r.method === 'PATCH')).toEqual([]);
  });

  it('shows no card when nothing is pending', () => {
    const { host } = mount([], 'classic', new FakeGitHub());
    expect(host.innerHTML).toBe('');
  });
});
