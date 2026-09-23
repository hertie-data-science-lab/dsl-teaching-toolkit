import { describe, expect, it } from 'vitest';
import { PatAuth, TOKEN_KEY, type TokenStore } from '../src/auth/pat';
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

  it('refuses a fine-grained token (no scopes header)', async () => {
    const gh = new FakeGitHub().on('GET', '/user', () => json(user));
    await expect(new PatAuth({ fetch: gh.fetch, store: store() }).signIn('t')).rejects.toThrow(/fine-grained/);
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
