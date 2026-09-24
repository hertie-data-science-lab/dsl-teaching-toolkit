import { describe, expect, it } from 'vitest';
import { PatAuth, TOKEN_KEY } from '../src/auth/pat';
import type { TokenStore } from '../src/auth/types';
import { FakeGitHub, json } from './fake';

function store(): TokenStore & { map: Map<string, string> } {
  const map = new Map<string, string>();
  return { map, getItem: (k) => map.get(k) ?? null, setItem: (k, v) => void map.set(k, v), removeItem: (k) => void map.delete(k) };
}

const user = { login: 'octo', id: 1, name: 'Octo Cat', email: null, avatar_url: 'a' };

describe('PatAuth', () => {
  it('validates a classic token with repo and workflow and keeps it in session storage only', async () => {
    const gh = new FakeGitHub().on('GET', '/user', () => json(user, 200, { 'x-oauth-scopes': 'repo, workflow, read:org' }));
    const s = store();
    const auth = new PatAuth({ fetch: gh.fetch, store: s });
    expect((await auth.signIn(' ghp_x ')).login).toBe('octo');
    expect(auth.token()).toBe('ghp_x');
    expect(s.map.get(TOKEN_KEY)).toBe('ghp_x');
    expect(gh.seen[0].headers.Authorization).toBe('Bearer ghp_x');
    auth.signOut();
    expect(auth.token()).toBeNull();
    expect(s.map.has(TOKEN_KEY)).toBe(false);
  });

  it('refuses a token without the workflow scope', async () => {
    const gh = new FakeGitHub().on('GET', '/user', () => json(user, 200, { 'x-oauth-scopes': 'repo' }));
    await expect(new PatAuth({ fetch: gh.fetch, store: store() }).signIn('t')).rejects.toThrow(/workflow scope/);
  });

  it('accepts a fine-grained token and says which organisations it cannot see', async () => {
    const gh = new FakeGitHub()
      .on('GET', '/user', () => json(user))
      .on('GET', '/user/orgs?per_page=100', () => json([]))
      .on('GET', '/users/octo/orgs?per_page=100', () => json([{ login: 'hertie-dsl-demo-f2026' }, { login: 'other-org' }]))
      .on('GET', '/user/memberships/orgs/hertie-dsl-demo-f2026', () => json({ state: 'active', role: 'admin' }))
      .on('GET', '/user/memberships/orgs/other-org', () => json({ message: 'Resource not accessible by personal access token' }, 403))
      .on('GET', '/repos/other-org/.github', () => json({ name: '.github', private: false }));
    const auth = new PatAuth({ fetch: gh.fetch, store: store() });
    expect((await auth.signIn('github_pat_x')).login).toBe('octo');
    expect(auth.token()).toBe('github_pat_x');
    expect(auth.reach()).toEqual({ seen: ['hertie-dsl-demo-f2026'], unseen: ['other-org'] });
    expect(gh.seen.every((r) => r.headers.Authorization === 'Bearer github_pat_x')).toBe(true);
    auth.signOut();
    expect(auth.reach()).toBeNull();
  });

  it('keeps the scope check for a classic token and probes nothing', async () => {
    const gh = new FakeGitHub().on('GET', '/user', () => json(user, 200, { 'x-oauth-scopes': 'repo, workflow' }));
    const auth = new PatAuth({ fetch: gh.fetch, store: store() });
    await auth.signIn('ghp_x');
    expect(auth.reach()).toBeNull();
    expect(gh.seen).toHaveLength(1);
  });

  it('says so when GitHub rejects the token', async () => {
    const gh = new FakeGitHub().on('GET', '/user', () => json({ message: 'Bad credentials' }, 401));
    await expect(new PatAuth({ fetch: gh.fetch, store: store() }).signIn('t')).rejects.toThrow(/did not accept/);
  });

  it('restores a remembered session, and forgets one that no longer works', async () => {
    const ok = new FakeGitHub().on('GET', '/user', () => json(user, 200, { 'x-oauth-scopes': 'repo, workflow' }));
    const s = store();
    s.setItem(TOKEN_KEY, 'saved');
    expect((await new PatAuth({ fetch: ok.fetch, store: s }).restore())?.login).toBe('octo');
    const bad = new FakeGitHub().on('GET', '/user', () => json({}, 401));
    expect(await new PatAuth({ fetch: bad.fetch, store: s }).restore()).toBeNull();
    expect(s.map.has(TOKEN_KEY)).toBe(false);
  });
});
