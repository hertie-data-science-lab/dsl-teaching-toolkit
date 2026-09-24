import { afterEach, describe, expect, it, vi } from 'vitest';
import relay, { type Env } from '../src/index';

const ORIGIN = 'https://console.example';
const env = (over: Partial<Env> = {}): Env => ({
  GH_APP_CLIENT_ID: 'Iv1.client',
  GH_APP_CLIENT_SECRET: 'shh',
  CONSOLE_ORIGIN: `${ORIGIN}, http://localhost:5173`,
  LIMITER: { limit: async () => ({ success: true }) },
  ...over,
});
const post = (path: string, body: unknown, origin = ORIGIN) =>
  new Request(`https://relay.example${path}`, { method: 'POST', headers: { Origin: origin, 'Content-Type': 'application/json', 'CF-Connecting-IP': '203.0.113.9' }, body: JSON.stringify(body) });

/** Stub GitHub's token endpoint; returns the form fields each call sent. */
function github(answer: object, status = 200) {
  const sent: URLSearchParams[] = [];
  vi.stubGlobal('fetch', async (url: string, init: RequestInit) => {
    expect(url).toBe('https://github.com/login/oauth/access_token');
    sent.push(new URLSearchParams(String(init.body)));
    return new Response(JSON.stringify(answer), { status, headers: { 'Content-Type': 'application/json' } });
  });
  return sent;
}

const TOKENS = { access_token: 'ghu_a', refresh_token: 'ghr_r', expires_in: 28800, refresh_token_expires_in: 15897600, token_type: 'bearer', scope: '' };

afterEach(() => vi.unstubAllGlobals());

describe('relay', () => {
  it('exchanges a code with the client secret and returns only the tokens', async () => {
    const sent = github(TOKENS);
    const res = await relay.fetch(post('/exchange', { code: 'abc123', code_verifier: 'v-._~x' }), env());
    expect(res.status).toBe(200);
    expect(res.headers.get('Access-Control-Allow-Origin')).toBe(ORIGIN);
    expect(res.headers.get('Cache-Control')).toBe('no-store');
    expect(await res.json()).toEqual({ access_token: 'ghu_a', refresh_token: 'ghr_r', expires_in: 28800, refresh_token_expires_in: 15897600 });
    expect(Object.fromEntries(sent[0])).toEqual({ client_id: 'Iv1.client', client_secret: 'shh', code: 'abc123', code_verifier: 'v-._~x' });
  });

  it('refreshes with grant_type refresh_token', async () => {
    const sent = github(TOKENS);
    const res = await relay.fetch(post('/refresh', { refresh_token: 'ghr_old' }, 'http://localhost:5173'), env());
    expect(res.status).toBe(200);
    expect(sent[0].get('grant_type')).toBe('refresh_token');
    expect(sent[0].get('refresh_token')).toBe('ghr_old');
  });

  it('passes GitHub’s own error through as a 400', async () => {
    github({ error: 'bad_verification_code', error_description: 'The code passed is incorrect or expired.' });
    const res = await relay.fetch(post('/exchange', { code: 'old' }), env());
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: 'bad_verification_code', error_description: 'The code passed is incorrect or expired.' });
  });

  it('answers 502 when GitHub fails or is unreachable', async () => {
    github({}, 503);
    expect((await relay.fetch(post('/exchange', { code: 'c' }), env())).status).toBe(502);
    vi.stubGlobal('fetch', async () => {
      throw new TypeError('network');
    });
    expect((await relay.fetch(post('/exchange', { code: 'c' }), env())).status).toBe(502);
  });

  it('refuses other origins, with no CORS headers', async () => {
    const sent = github(TOKENS);
    const res = await relay.fetch(post('/exchange', { code: 'c' }, 'https://evil.example'), env());
    expect(res.status).toBe(403);
    expect(res.headers.get('Access-Control-Allow-Origin')).toBeNull();
    expect(sent).toHaveLength(0);
  });

  it('answers the preflight for the console origin only', async () => {
    const pre = (origin: string) => new Request('https://relay.example/exchange', { method: 'OPTIONS', headers: { Origin: origin } });
    const ok = await relay.fetch(pre(ORIGIN), env());
    expect(ok.status).toBe(204);
    expect(ok.headers.get('Access-Control-Allow-Methods')).toBe('POST');
    expect((await relay.fetch(pre('https://evil.example'), env())).status).toBe(403);
  });

  it('refuses unknown routes, malformed bodies and odd credentials without calling GitHub', async () => {
    const sent = github(TOKENS);
    expect((await relay.fetch(post('/other', { code: 'c' }), env())).status).toBe(404);
    expect((await relay.fetch(post('/exchange', {}), env())).status).toBe(400);
    expect((await relay.fetch(post('/exchange', { code: 'a b' }), env())).status).toBe(400);
    expect((await relay.fetch(post('/refresh', { refresh_token: 1 }), env())).status).toBe(400);
    const junk = new Request('https://relay.example/exchange', { method: 'POST', headers: { Origin: ORIGIN }, body: 'not json' });
    expect((await relay.fetch(junk, env())).status).toBe(400);
    expect(sent).toHaveLength(0);
  });

  it('rate-limits per client address', async () => {
    const sent = github(TOKENS);
    const keys: string[] = [];
    const limited = env({ LIMITER: { limit: async ({ key }) => (keys.push(key), { success: false }) } });
    expect((await relay.fetch(post('/exchange', { code: 'c' }), limited)).status).toBe(429);
    expect(keys).toEqual(['203.0.113.9']);
    expect(sent).toHaveLength(0);
  });

  it('says it is not configured when the client id or secret is missing', async () => {
    github(TOKENS);
    expect((await relay.fetch(post('/exchange', { code: 'c' }), env({ GH_APP_CLIENT_SECRET: '' }))).status).toBe(500);
  });
});
