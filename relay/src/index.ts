// The console's sign-in relay: a Cloudflare Worker that turns a GitHub App sign-in code (or a
// refresh token) into a user access token. GitHub's token endpoint needs the App's client
// secret and sends no CORS headers, so a static page cannot call it; this does, and nothing
// else. No state, no storage, no logging: the tokens pass through and are forgotten.

const TOKEN_URL = 'https://github.com/login/oauth/access_token';
// A sign-in code, a PKCE verifier or a refresh token: letters, digits and `-._~` only.
const CREDENTIAL = /^[\w.~-]{1,512}$/;

/** Cloudflare's rate-limiting binding (`[[ratelimits]]` in wrangler.toml). */
export interface RateLimit {
  limit(options: { key: string }): Promise<{ success: boolean }>;
}

export interface Env {
  GH_APP_CLIENT_ID: string;
  GH_APP_CLIENT_SECRET: string;
  /** The console's origin, e.g. https://hertie-data-science-lab.github.io; comma-separate several. */
  CONSOLE_ORIGIN: string;
  LIMITER: RateLimit;
}

function reply(status: number, body: object, headers: Record<string, string>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...headers, 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
  });
}

async function handle(req: Request, env: Env): Promise<Response> {
  const origin = req.headers.get('Origin') ?? '';
  const allowed = env.CONSOLE_ORIGIN.split(',').map((o) => o.trim()).filter(Boolean);
  if (!allowed.includes(origin)) return new Response('Forbidden', { status: 403 });
  const cors = { 'Access-Control-Allow-Origin': origin, Vary: 'Origin' };
  if (req.method === 'OPTIONS') {
    return new Response(null, {
      status: 204,
      headers: { ...cors, 'Access-Control-Allow-Methods': 'POST', 'Access-Control-Allow-Headers': 'Content-Type', 'Access-Control-Max-Age': '86400' },
    });
  }
  const route = new URL(req.url).pathname;
  if (req.method !== 'POST' || (route !== '/exchange' && route !== '/refresh')) return reply(404, { error: 'not_found' }, cors);
  if (!env.GH_APP_CLIENT_ID || !env.GH_APP_CLIENT_SECRET) return reply(500, { error: 'not_configured' }, cors);

  const ip = req.headers.get('CF-Connecting-IP') ?? 'unknown';
  if (!(await env.LIMITER.limit({ key: ip })).success) return reply(429, { error: 'rate_limited' }, cors);

  let body: Record<string, unknown>;
  try {
    body = (await req.json()) as Record<string, unknown>;
  } catch {
    return reply(400, { error: 'bad_request' }, cors);
  }
  const valid = (v: unknown): v is string => typeof v === 'string' && CREDENTIAL.test(v);
  const params = new URLSearchParams({ client_id: env.GH_APP_CLIENT_ID, client_secret: env.GH_APP_CLIENT_SECRET });
  if (route === '/exchange') {
    if (!valid(body.code)) return reply(400, { error: 'bad_request' }, cors);
    params.set('code', body.code);
    if (body.code_verifier !== undefined) {
      if (!valid(body.code_verifier)) return reply(400, { error: 'bad_request' }, cors);
      params.set('code_verifier', body.code_verifier);
    }
  } else {
    if (!valid(body.refresh_token)) return reply(400, { error: 'bad_request' }, cors);
    params.set('grant_type', 'refresh_token');
    params.set('refresh_token', body.refresh_token);
  }

  let res: Response;
  try {
    res = await fetch(TOKEN_URL, {
      method: 'POST',
      headers: { Accept: 'application/json', 'Content-Type': 'application/x-www-form-urlencoded', 'User-Agent': 'dsl-console-auth-relay' },
      body: params,
    });
  } catch {
    return reply(502, { error: 'github_unreachable' }, cors);
  }
  const out = (res.ok ? await res.json().catch(() => ({})) : {}) as Record<string, unknown>;
  // GitHub answers a bad code or refresh token with 200 and an `error` field.
  if (typeof out.error === 'string') return reply(400, { error: out.error, error_description: String(out.error_description ?? '') }, cors);
  if (typeof out.access_token !== 'string') return reply(502, { error: 'github_error' }, cors);
  return reply(200, { access_token: out.access_token, refresh_token: out.refresh_token, expires_in: out.expires_in, refresh_token_expires_in: out.refresh_token_expires_in }, cors);
}

export default { fetch: handle };
