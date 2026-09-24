import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { APP_PENDING_KEY, APP_SESSION_KEY, AppAuth, AUTHORIZE_URL, NOT_CONFIGURED, type AppAuthOptions } from '../src/auth/app';
import { ConsoleAuth } from '../src/auth/console';
import { PatAuth, TOKEN_KEY } from '../src/auth/pat';
import type { TokenStore } from '../src/auth/types';
import { FakeGitHub, json } from './fake';

const RELAY = 'https://relay.example';
const HOME = 'https://console.example/app/';
const user = { login: 'octo', id: 1, name: 'Octo Cat', email: null, avatar_url: 'a' };
const HOUR = 3600 * 1000;

function store(): TokenStore & { map: Map<string, string> } {
  const map = new Map<string, string>();
  return { map, getItem: (k) => map.get(k) ?? null, setItem: (k, v) => void map.set(k, v), removeItem: (k) => void map.delete(k) };
}

/** GitHub and the relay behind one fake fetch; the relay hands out numbered tokens. */
function world() {
  let n = 0;
  const gh = new FakeGitHub()
    .on('GET', '/user', () => json(user))
    .on('POST', /^https:\/\/relay\.example\/(exchange|refresh)$/, () => (n++, json({ access_token: `ghu_${n}`, refresh_token: `ghr_${n}`, expires_in: 28800 })));
  const relayCalls = () => gh.seen.filter((r) => r.url.startsWith(RELAY));
  return { gh, relayCalls };
}

function app(over: Partial<AppAuthOptions> & { href?: string } = {}) {
  const s = over.store ?? store();
  const urls: { replaced: string[]; navigated: string[] } = { replaced: [], navigated: [] };
  const auth = new AppAuth({
    clientId: 'Iv1.client',
    relayUrl: `${RELAY}/`,
    redirectUri: HOME,
    store: s,
    location: () => over.href ?? HOME,
    replaceUrl: (u) => urls.replaced.push(u),
    navigate: (u) => urls.navigated.push(u),
    ...over,
  });
  return { auth, store: s as ReturnType<typeof store>, urls };
}

const b64url = (b: ArrayBuffer) => btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

describe('AppAuth', () => {
  beforeEach(() => vi.useFakeTimers({ now: Date.parse('2026-09-24T09:00:00Z') }));
  afterEach(() => vi.useRealTimers());

  it('sends the browser to GitHub with the client id, a state and a PKCE challenge', async () => {
    const { auth, store: s, urls } = app({ href: `${HOME}?cohort=o#people` });
    void auth.signIn();
    await vi.waitFor(() => expect(urls.navigated).toHaveLength(1));
    const to = new URL(urls.navigated[0]);
    expect(`${to.origin}${to.pathname}`).toBe(AUTHORIZE_URL);
    expect(to.searchParams.get('client_id')).toBe('Iv1.client');
    expect(to.searchParams.get('redirect_uri')).toBe(HOME);
    expect(to.searchParams.get('code_challenge_method')).toBe('S256');
    const pending = JSON.parse(s.map.get(APP_PENDING_KEY)!);
    expect(to.searchParams.get('state')).toBe(pending.state);
    expect(pending.back).toBe(`${HOME}?cohort=o#people`);
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(pending.verifier));
    expect(to.searchParams.get('code_challenge')).toBe(b64url(digest));
  });

  it('takes the code off the URL, exchanges it through the relay and keeps the tokens in session storage', async () => {
    const { gh, relayCalls } = world();
    const s = store();
    s.setItem(APP_PENDING_KEY, JSON.stringify({ state: 'S', verifier: 'V', back: `${HOME}?cohort=o#people` }));
    const { auth, urls } = app({ fetch: gh.fetch, store: s, href: `${HOME}?code=C&state=S` });
    auth.takeCallback();
    expect(urls.replaced).toEqual([`${HOME}?cohort=o#people`]);
    expect(s.map.has(APP_PENDING_KEY)).toBe(false);
    expect((await auth.restore())?.login).toBe('octo');
    expect(relayCalls()[0].url).toBe(`${RELAY}/exchange`);
    expect(relayCalls()[0].body).toEqual({ code: 'C', code_verifier: 'V', redirect_uri: HOME });
    expect(auth.token()).toBe('ghu_1');
    expect(gh.seen.find((r) => r.url.endsWith('/user'))?.headers.Authorization).toBe('Bearer ghu_1');
    expect(JSON.parse(s.map.get(APP_SESSION_KEY)!)).toMatchObject({ access_token: 'ghu_1', refresh_token: 'ghr_1', expires_at: Date.now() + 8 * HOUR });
    auth.signOut();
    expect(auth.token()).toBeNull();
    expect(s.map.has(APP_SESSION_KEY)).toBe(false);
  });

  it('refuses a callback whose state does not match, without calling the relay', async () => {
    const { gh, relayCalls } = world();
    const s = store();
    s.setItem(APP_PENDING_KEY, JSON.stringify({ state: 'S', verifier: 'V', back: HOME }));
    const { auth } = app({ fetch: gh.fetch, store: s, href: `${HOME}?code=C&state=forged` });
    auth.takeCallback();
    await expect(auth.restore()).rejects.toThrow(/did not match/);
    expect(relayCalls()).toHaveLength(0);
    expect(auth.token()).toBeNull();
  });

  it('says so when the sign-in was cancelled on GitHub', async () => {
    const s = store();
    s.setItem(APP_PENDING_KEY, JSON.stringify({ state: 'S', verifier: 'V', back: HOME }));
    const { auth } = app({ store: s, href: `${HOME}?error=access_denied&state=S` });
    auth.takeCallback();
    await expect(auth.restore()).rejects.toThrow(/cancelled/);
  });

  it('leaves a URL without a callback alone', () => {
    const { auth, urls } = app({ href: `${HOME}?cohort=o#people` });
    auth.takeCallback();
    expect(urls.replaced).toEqual([]);
  });

  it('refreshes five minutes before the access token runs out', async () => {
    const { gh, relayCalls } = world();
    const s = store();
    s.setItem(APP_PENDING_KEY, JSON.stringify({ state: 'S', verifier: 'V', back: HOME }));
    const { auth } = app({ fetch: gh.fetch, store: s, href: `${HOME}?code=C&state=S` });
    auth.takeCallback();
    await auth.restore();
    await vi.advanceTimersByTimeAsync(8 * HOUR - 6 * 60 * 1000);
    expect(relayCalls()).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(60 * 1000);
    expect(relayCalls()[1].url).toBe(`${RELAY}/refresh`);
    expect(relayCalls()[1].body).toEqual({ refresh_token: 'ghr_1' });
    expect(auth.token()).toBe('ghu_2');
    expect(JSON.parse(s.map.get(APP_SESSION_KEY)!).refresh_token).toBe('ghr_2');
  });

  it('ends the session when GitHub refuses the refresh token', async () => {
    const gh = new FakeGitHub()
      .on('GET', '/user', () => json(user))
      .on('POST', `${RELAY}/refresh`, () => json({ error: 'bad_refresh_token', error_description: 'The refresh token passed is incorrect or expired.' }, 400));
    const s = store();
    s.setItem(APP_SESSION_KEY, JSON.stringify({ access_token: 'ghu_old', refresh_token: 'ghr_old', expires_at: Date.now() + HOUR }));
    const onLost = vi.fn();
    const { auth } = app({ fetch: gh.fetch, store: s, onLost });
    expect((await auth.restore())?.login).toBe('octo');
    await vi.advanceTimersByTimeAsync(HOUR);
    expect(onLost).toHaveBeenCalledOnce();
    expect(auth.token()).toBeNull();
    expect(s.map.has(APP_SESSION_KEY)).toBe(false);
  });

  it('keeps the session through network failures and retries after 30s, 2m, then 10m', async () => {
    let calls = 0;
    const base = new FakeGitHub()
      .on('GET', '/user', () => json(user))
      .on('POST', `${RELAY}/refresh`, () => json({ access_token: 'ghu_new', refresh_token: 'ghr_new', expires_in: 28800 }));
    const fetch = (url: string, init?: RequestInit) => {
      if (url.startsWith(RELAY) && ++calls <= 3) return Promise.reject(new TypeError('Failed to fetch'));
      return base.fetch(url, init);
    };
    const s = store();
    s.setItem(APP_SESSION_KEY, JSON.stringify({ access_token: 'ghu_old', refresh_token: 'ghr_old', expires_at: Date.now() + HOUR }));
    const onLost = vi.fn();
    const { auth } = app({ fetch, store: s, onLost });
    await auth.restore();
    await vi.advanceTimersByTimeAsync(55 * 60 * 1000); // the scheduled refresh: fails
    expect(calls).toBe(1);
    await vi.advanceTimersByTimeAsync(30 * 1000); // first retry: fails
    expect(calls).toBe(2);
    await vi.advanceTimersByTimeAsync(2 * 60 * 1000 - 1);
    expect(calls).toBe(2);
    await vi.advanceTimersByTimeAsync(1); // second retry: fails
    expect(calls).toBe(3);
    expect(auth.token()).toBe('ghu_old');
    expect(s.map.has(APP_SESSION_KEY)).toBe(true);
    await vi.advanceTimersByTimeAsync(10 * 60 * 1000); // third retry: works
    expect(auth.token()).toBe('ghu_new');
    expect(onLost).not.toHaveBeenCalled();
  });

  it('retries a 5xx from the relay, and signs out on another 4xx', async () => {
    let status = 502;
    const gh = new FakeGitHub()
      .on('GET', '/user', () => json(user))
      .on('POST', `${RELAY}/refresh`, () => json({ error: 'x' }, status));
    const s = store();
    s.setItem(APP_SESSION_KEY, JSON.stringify({ access_token: 'ghu_old', refresh_token: 'ghr_old', expires_at: Date.now() + HOUR }));
    const onLost = vi.fn();
    const { auth } = app({ fetch: gh.fetch, store: s, onLost });
    await auth.restore();
    await vi.advanceTimersByTimeAsync(55 * 60 * 1000);
    expect(auth.token()).toBe('ghu_old');
    status = 403;
    await vi.advanceTimersByTimeAsync(30 * 1000);
    expect(onLost).toHaveBeenCalledOnce();
    expect(auth.token()).toBeNull();
  });

  it('says sign-in is not set up when the relay answers not_configured', async () => {
    const gh = new FakeGitHub().on('POST', `${RELAY}/exchange`, () => json({ error: 'not_configured' }, 503));
    const s = store();
    s.setItem(APP_PENDING_KEY, JSON.stringify({ state: 'S', verifier: 'V', back: HOME }));
    const { auth } = app({ fetch: gh.fetch, store: s, href: `${HOME}?code=C&state=S` });
    auth.takeCallback();
    await expect(auth.restore()).rejects.toThrow(NOT_CONFIGURED);
  });

  it('uses a saved session that is about to run out when the refresh gets no answer, and keeps trying', async () => {
    let calls = 0;
    const base = world();
    const fetch = (url: string, init?: RequestInit) => (url.startsWith(RELAY) && ++calls === 1 ? Promise.reject(new TypeError('offline')) : base.gh.fetch(url, init));
    const s = store();
    s.setItem(APP_SESSION_KEY, JSON.stringify({ access_token: 'ghu_old', refresh_token: 'ghr_old', expires_at: Date.now() + 60 * 1000 }));
    const { auth } = app({ fetch, store: s });
    expect((await auth.restore())?.login).toBe('octo');
    expect(auth.token()).toBe('ghu_old');
    await vi.advanceTimersByTimeAsync(30 * 1000);
    expect(auth.token()).toBe('ghu_1');
  });

  it('refreshes a saved session that is about to run out before using it', async () => {
    const { gh, relayCalls } = world();
    const s = store();
    s.setItem(APP_SESSION_KEY, JSON.stringify({ access_token: 'ghu_old', refresh_token: 'ghr_old', expires_at: Date.now() + 60 * 1000 }));
    const { auth } = app({ fetch: gh.fetch, store: s });
    expect((await auth.restore())?.login).toBe('octo');
    expect(relayCalls()[0].body).toEqual({ refresh_token: 'ghr_old' });
    expect(auth.token()).toBe('ghu_1');
  });

  it('uses a saved session that still has time, and drops one GitHub no longer accepts', async () => {
    const { gh, relayCalls } = world();
    const s = store();
    s.setItem(APP_SESSION_KEY, JSON.stringify({ access_token: 'ghu_saved', refresh_token: 'ghr_saved', expires_at: Date.now() + 4 * HOUR }));
    const ok = app({ fetch: gh.fetch, store: s }).auth;
    expect((await ok.restore())?.login).toBe('octo');
    expect(ok.token()).toBe('ghu_saved');
    expect(relayCalls()).toHaveLength(0);
    const bad = new FakeGitHub().on('GET', '/user', () => json({}, 401));
    expect(await app({ fetch: bad.fetch, store: s }).auth.restore()).toBeNull();
    expect(s.map.has(APP_SESSION_KEY)).toBe(false);
  });
});

describe('ConsoleAuth', () => {
  it('falls back to a remembered token, and answers with whichever signed in', async () => {
    const gh = new FakeGitHub().on('GET', '/user', () => json(user, 200, { 'x-oauth-scopes': 'repo, workflow' }));
    const s = store();
    s.setItem(TOKEN_KEY, 'ghp_saved');
    const auth = new ConsoleAuth(new PatAuth({ fetch: gh.fetch, store: s }), app({ store: s }).auth);
    expect(auth.token()).toBeNull();
    expect((await auth.restore())?.login).toBe('octo');
    expect(auth.token()).toBe('ghp_saved');
    auth.signOut();
    expect(auth.token()).toBeNull();
    expect(s.map.size).toBe(0);
  });

  it('keeps a failed GitHub sign-in as a notice instead of throwing', async () => {
    const s = store();
    s.setItem(APP_PENDING_KEY, JSON.stringify({ state: 'S', verifier: 'V', back: HOME }));
    const a = app({ store: s, href: `${HOME}?code=C&state=forged` }).auth;
    a.takeCallback();
    const auth = new ConsoleAuth(new PatAuth({ store: s }), a);
    expect(await auth.restore()).toBeNull();
    expect(auth.notice).toMatch(/did not match/);
  });

  it('signs out of both paths, clearing both stores', async () => {
    const gh = new FakeGitHub().on('GET', '/user', () => json(user, 200, { 'x-oauth-scopes': 'repo, workflow' }));
    const patStore = store();
    const appStore = store();
    patStore.setItem(TOKEN_KEY, 'ghp_saved');
    appStore.setItem(APP_SESSION_KEY, JSON.stringify({ access_token: 'ghu_saved', refresh_token: 'ghr_saved', expires_at: Date.now() + 4 * HOUR }));
    const auth = new ConsoleAuth(new PatAuth({ fetch: gh.fetch, store: patStore }), app({ fetch: gh.fetch, store: appStore }).auth);
    await auth.restore();
    expect(auth.token()).toBe('ghu_saved');
    auth.signOut();
    expect(auth.token()).toBeNull();
    expect(patStore.map.size).toBe(0);
    expect(appStore.map.size).toBe(0);
  });

  it('has no GitHub path without an App', async () => {
    const auth = new ConsoleAuth(new PatAuth({ store: null }), null);
    await expect(auth.signInWithApp()).rejects.toThrow(/not set up/);
  });
});
