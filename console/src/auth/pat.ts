import { GitHubError, type Fetch, type GhUser, API } from '../github/client';
import { SignInError, browserStore, type Auth, type TokenStore } from './types';

export const TOKEN_KEY = 'dsl-console-token';
export const REQUIRED_SCOPES = ['repo', 'workflow'];
export const NEW_TOKEN_URL =
  'https://github.com/settings/tokens/new?scopes=repo,workflow&description=DSL%20Instructor%20Console';

/**
 * Sign-in with a pasted classic personal access token carrying `repo` and `workflow`.
 * The token lives in sessionStorage only: it is gone when the tab closes, and it is never
 * written anywhere else.
 */
export class PatAuth implements Auth {
  private tok: string | null = null;
  private who: GhUser | null = null;
  private readonly store: TokenStore | null;
  private readonly fetchFn: Fetch;

  constructor(opts: { fetch?: Fetch; store?: TokenStore | null } = {}) {
    this.fetchFn = opts.fetch ?? ((i, init) => globalThis.fetch(i, init));
    this.store = opts.store === undefined ? browserStore() : opts.store;
  }

  token(): string | null {
    return this.tok;
  }

  user(): GhUser | null {
    return this.who;
  }

  async signIn(credential?: string): Promise<GhUser> {
    const t = (credential ?? '').trim();
    if (!t) throw new SignInError('Paste a token first.');
    const res = await this.fetchFn(`${API}/user`, {
      headers: { Accept: 'application/vnd.github+json', Authorization: `Bearer ${t}` },
    });
    if (res.status === 401) throw new SignInError('GitHub did not accept this token. Check it was copied whole and has not expired.');
    if (!res.ok) throw new GitHubError(res.status, `GitHub answered ${res.status} while checking the token.`, `${API}/user`);
    // Classic tokens list their scopes; fine-grained tokens send no header and are refused,
    // because they cannot hold `workflow` across orgs the way the console needs.
    const scopes = (res.headers.get('x-oauth-scopes') ?? '').split(',').map((s) => s.trim()).filter(Boolean);
    const missing = REQUIRED_SCOPES.filter((s) => !scopes.includes(s));
    if (res.headers.get('x-oauth-scopes') === null) throw new SignInError('This looks like a fine-grained token. Create a classic token with the repo and workflow scopes.');
    if (missing.length) throw new SignInError(`This token is missing the ${missing.join(' and ')} scope${missing.length > 1 ? 's' : ''}.`);
    const user = (await res.json()) as GhUser;
    this.tok = t;
    this.who = user;
    try {
      this.store?.setItem(TOKEN_KEY, t);
    } catch {
      /* storage unavailable: the session lasts until reload */
    }
    return user;
  }

  signOut(): void {
    this.tok = null;
    this.who = null;
    try {
      this.store?.removeItem(TOKEN_KEY);
    } catch {
      /* ignore */
    }
  }

  async restore(): Promise<GhUser | null> {
    let saved: string | null = null;
    try {
      saved = this.store?.getItem(TOKEN_KEY) ?? null;
    } catch {
      saved = null;
    }
    if (!saved) return null;
    try {
      return await this.signIn(saved);
    } catch {
      this.signOut();
      return null;
    }
  }
}
