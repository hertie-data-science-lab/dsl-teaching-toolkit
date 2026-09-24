import { API, GitHubError, type Fetch, type GhUser } from '../github/client';
import { SignInError, browserStore, type Auth, type TokenStore } from './types';

export const AUTHORIZE_URL = 'https://github.com/login/oauth/authorize';
export const APP_SESSION_KEY = 'dsl-console-app-session';
export const APP_PENDING_KEY = 'dsl-console-app-pending';
/** Refresh this long before the access token (8 hours) runs out. */
const REFRESH_EARLY_MS = 5 * 60 * 1000;
/** After a refresh that got no answer, try again after these waits (the last repeats). */
const BACKOFF_MS = [30 * 1000, 2 * 60 * 1000, 10 * 60 * 1000];
export const NOT_CONFIGURED = 'Sign-in is not set up on this deployment; use a token.';

interface Session {
  access_token: string;
  refresh_token: string; // '' when the App's tokens do not expire
  expires_at: number; // epoch ms; 0 when the App's tokens do not expire
}

interface Pending {
  state: string;
  verifier: string;
  back: string; // the console URL to return to, search and hash included
}

export interface AppAuthOptions {
  clientId: string;
  relayUrl: string;
  /** Where GitHub sends the browser back: the console's own URL, as registered on the App. */
  redirectUri: string;
  fetch?: Fetch;
  store?: TokenStore | null;
  /** The current URL, and a way to replace it without a reload (history.replaceState). */
  location?: () => string;
  replaceUrl?: (url: string) => void;
  navigate?: (url: string) => void;
  /** Called when the session ends by itself (the refresh token was refused). */
  onLost?: () => void;
}

function b64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function random(n = 32): string {
  return b64url(crypto.getRandomValues(new Uint8Array(n)));
}

async function challenge(verifier: string): Promise<string> {
  return b64url(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))));
}

/**
 * GitHub App user-to-server sign-in (decision 0002), web flow: redirect to GitHub with a
 * random `state` and a PKCE challenge, take the `code` back on the console's own URL, and
 * have the relay (relay/) exchange it, because GitHub's token endpoint needs the App's
 * client secret and sends no CORS headers. Tokens live in sessionStorage only and are
 * refreshed shortly before the 8-hour access token runs out.
 */
export class AppAuth implements Auth {
  private session: Session | null = null;
  private who: GhUser | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private failures = 0;
  private callback: { code: string; verifier: string } | { error: string } | null = null;
  private readonly opts: AppAuthOptions;
  private readonly fetchFn: Fetch;
  private readonly store: TokenStore | null;

  constructor(opts: AppAuthOptions) {
    this.opts = opts;
    this.fetchFn = opts.fetch ?? ((i, init) => globalThis.fetch(i, init));
    this.store = opts.store === undefined ? browserStore() : opts.store;
  }

  token(): string | null {
    return this.session?.access_token ?? null;
  }

  user(): GhUser | null {
    return this.who;
  }

  /**
   * Take GitHub's `?code=&state=` off the URL before the app reads it, and put back the
   * console URL the sign-in started from. Run once at start-up, before the router.
   */
  takeCallback(): void {
    const here = new URL(this.opts.location?.() ?? globalThis.location.href);
    const code = here.searchParams.get('code');
    const state = here.searchParams.get('state');
    const error = here.searchParams.get('error');
    if (!state || (!code && !error)) return;
    const pending = this.read<Pending>(APP_PENDING_KEY);
    this.remove(APP_PENDING_KEY);
    for (const k of ['code', 'state', 'error', 'error_description', 'error_uri']) here.searchParams.delete(k);
    this.replace(pending?.back ?? here.toString());
    if (!pending || pending.state !== state) this.callback = { error: 'The sign-in answer from GitHub did not match this tab. Try again.' };
    else if (error) this.callback = { error: error === 'access_denied' ? 'Sign-in was cancelled on GitHub.' : `GitHub did not sign you in (${error}).` };
    else if (code) this.callback = { code, verifier: pending.verifier };
  }

  /** Go to GitHub to sign in. The page leaves, so the promise never settles. */
  async signIn(): Promise<GhUser> {
    const pending: Pending = { state: random(), verifier: random(), back: this.opts.location?.() ?? globalThis.location.href };
    this.write(APP_PENDING_KEY, pending);
    const url = new URL(AUTHORIZE_URL);
    url.searchParams.set('client_id', this.opts.clientId);
    url.searchParams.set('redirect_uri', this.opts.redirectUri);
    url.searchParams.set('state', pending.state);
    url.searchParams.set('code_challenge', await challenge(pending.verifier));
    url.searchParams.set('code_challenge_method', 'S256');
    (this.opts.navigate ?? ((u) => globalThis.location.assign(u)))(url.toString());
    return new Promise<GhUser>(() => {});
  }

  signOut(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.session = null;
    this.who = null;
    this.remove(APP_SESSION_KEY);
  }

  /**
   * Finish a sign-in GitHub just sent back (see takeCallback), or pick up this tab's
   * session. Throws a SignInError only for a callback that failed, so the sign-in screen
   * can say why; a stale saved session is dropped quietly.
   */
  async restore(): Promise<GhUser | null> {
    const cb = this.callback;
    this.callback = null;
    if (cb && 'error' in cb) throw new SignInError(cb.error);
    if (cb) return this.start(await this.relay('/exchange', { code: cb.code, code_verifier: cb.verifier, redirect_uri: this.opts.redirectUri }));
    const saved = this.read<Session>(APP_SESSION_KEY);
    if (!saved) return null;
    try {
      if (saved.refresh_token && saved.expires_at - Date.now() < REFRESH_EARLY_MS) {
        try {
          return await this.start(await this.relay('/refresh', { refresh_token: saved.refresh_token }));
        } catch (e) {
          // No answer, but the access token still works: use it and keep trying to refresh.
          if (e instanceof SignInError || saved.expires_at <= Date.now()) throw e;
          const u = await this.start(saved);
          this.retryLater();
          return u;
        }
      }
      return await this.start(saved);
    } catch (e) {
      // Refused (by GitHub or the relay): the session is over. No answer: keep it for a reload.
      if (e instanceof SignInError || e instanceof GitHubError) this.signOut();
      return null;
    }
  }

  private async start(s: Session): Promise<GhUser> {
    const res = await this.fetchFn(`${API}/user`, { headers: { Accept: 'application/vnd.github+json', Authorization: `Bearer ${s.access_token}` } });
    if (!res.ok) throw new GitHubError(res.status, `GitHub answered ${res.status} while reading your account.`, `${API}/user`);
    this.who = (await res.json()) as GhUser;
    this.keep(s);
    return this.who;
  }

  private keep(s: Session): void {
    this.session = s;
    this.failures = 0;
    this.write(APP_SESSION_KEY, s);
    if (s.refresh_token && s.expires_at) this.schedule(s.expires_at - Date.now() - REFRESH_EARLY_MS);
  }

  private schedule(ms: number): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = setTimeout(() => void this.refresh(), Math.max(0, ms));
  }

  private retryLater(): void {
    this.schedule(BACKOFF_MS[Math.min(this.failures, BACKOFF_MS.length - 1)]);
    this.failures++;
  }

  private async refresh(): Promise<void> {
    const s = this.session;
    if (!s) return;
    try {
      // A refresh token works once: the old pair dies when the new one is issued. A tab
      // duplicated from this one carries a copy of this sessionStorage, so whichever tab
      // refreshes second is refused (bad_refresh_token) and signs out; signing in again
      // there is the fix.
      this.keep(await this.relay('/refresh', { refresh_token: s.refresh_token }));
    } catch (e) {
      if (e instanceof SignInError) {
        // The relay answered and refused (GitHub said bad_refresh_token, or a 4xx): over.
        this.signOut();
        this.opts.onLost?.();
      } else {
        // No answer (offline, relay down, rate-limited): keep the session and try again.
        this.retryLater();
      }
    }
  }

  /**
   * POST to the relay. A SignInError when it answered with a refusal (a 4xx other than
   * 429); a plain Error when there was no usable answer, which is worth retrying.
   */
  private async relay(path: '/exchange' | '/refresh', body: Record<string, string>): Promise<Session> {
    const res = await this.fetchFn(`${this.opts.relayUrl.replace(/\/+$/, '')}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const out = (await res.json().catch(() => ({}))) as { access_token?: string; refresh_token?: string; expires_in?: number; error?: string; error_description?: string };
    if (res.status === 503 && out.error === 'not_configured') throw new Error(NOT_CONFIGURED);
    if (res.status >= 400 && res.status < 500 && res.status !== 429) throw new SignInError(out.error_description || `GitHub did not accept the sign-in (${out.error ?? `status ${res.status}`}). Try again.`);
    if (!res.ok || !out.access_token) throw new Error(`The sign-in relay answered ${res.status}.`);
    return { access_token: out.access_token, refresh_token: out.refresh_token ?? '', expires_at: out.expires_in ? Date.now() + out.expires_in * 1000 : 0 };
  }

  private replace(url: string): void {
    (this.opts.replaceUrl ?? ((u) => globalThis.history.replaceState(null, '', u)))(url);
  }

  private read<T>(key: string): T | null {
    try {
      const v = this.store?.getItem(key);
      return v ? (JSON.parse(v) as T) : null;
    } catch {
      return null;
    }
  }

  private write(key: string, value: unknown): void {
    try {
      this.store?.setItem(key, JSON.stringify(value));
    } catch {
      /* storage unavailable: the session lasts until reload */
    }
  }

  private remove(key: string): void {
    try {
      this.store?.removeItem(key);
    } catch {
      /* ignore */
    }
  }
}
