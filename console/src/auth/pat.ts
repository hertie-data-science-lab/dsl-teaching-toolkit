import { GitHubError, type Fetch, type GhUser, API } from '../github/client';
import { SignInError, browserStore, type Auth, type TokenStore } from './types';

export const TOKEN_KEY = 'dsl-console-token';
export const REQUIRED_SCOPES = ['repo', 'workflow'];
export const NEW_TOKEN_URL =
  'https://github.com/settings/tokens/new?scopes=repo,workflow&description=DSL%20Instructor%20Console';
export const NEW_FINE_GRAINED_URL = 'https://github.com/settings/personal-access-tokens/new';
/** At most this many organisations are probed for a fine-grained token. */
const PROBE_LIMIT = 50;

/** Which of the account's organisations a fine-grained token can reach. */
export interface Reach {
  seen: string[];
  unseen: string[];
}

/**
 * Sign-in with a pasted personal access token, the no-server fallback (decision 0011).
 * Classic tokens carry `repo` and `workflow`; fine-grained tokens are accepted and probed
 * for the organisations they reach. The token lives in sessionStorage only: it is gone when
 * the tab closes, and it is never written anywhere else.
 */
export class PatAuth implements Auth {
  private tok: string | null = null;
  private who: GhUser | null = null;
  private seen: Reach | null = null;
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

  /** For a fine-grained token, the organisations it can and cannot reach; null for a classic token. */
  reach(): Reach | null {
    return this.seen;
  }

  async signIn(credential?: string): Promise<GhUser> {
    const t = (credential ?? '').trim();
    if (!t) throw new SignInError('Paste a token first.');
    const res = await this.fetchFn(`${API}/user`, {
      headers: { Accept: 'application/vnd.github+json', Authorization: `Bearer ${t}` },
    });
    if (res.status === 401) throw new SignInError('GitHub did not accept this token. Check it was copied whole and has not expired.');
    if (!res.ok) throw new GitHubError(res.status, `GitHub answered ${res.status} while checking the token.`, `${API}/user`);
    // Classic tokens list their scopes in a header; fine-grained tokens send none.
    const header = res.headers.get('x-oauth-scopes');
    if (header !== null) {
      // Classic: today's check stands, `repo` and `workflow` both. `workflow` is needed only
      // to dispatch operations, which only instructors do; narrow it when the student
      // screens arrive.
      const scopes = header.split(',').map((s) => s.trim()).filter(Boolean);
      const missing = REQUIRED_SCOPES.filter((s) => !scopes.includes(s));
      if (missing.length) throw new SignInError(`This token is missing the ${missing.join(' and ')} scope${missing.length > 1 ? 's' : ''}.`);
    }
    const user = (await res.json()) as GhUser;
    this.seen = header === null ? await this.probe(t, user.login) : null;
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
    this.seen = null;
    try {
      this.store?.removeItem(TOKEN_KEY);
    } catch {
      /* ignore */
    }
  }

  /**
   * A fine-grained token reaches one owner's resources, and GitHub answers `GET /user/orgs`
   * for it with an empty list. So the organisations to check are that list joined with the
   * account's public memberships, and each is probed with the token's own membership read
   * (needs Members: read), which fails for an organisation the token cannot reach, unlike a
   * read of its public `.github` repo, which any token may make.
   */
  private async probe(t: string, login: string): Promise<Reach> {
    const get = (path: string) => this.fetchFn(`${API}${path}`, { headers: { Accept: 'application/vnd.github+json', Authorization: `Bearer ${t}` } });
    const list = async (path: string): Promise<string[]> => {
      const r = await get(path);
      return r.ok ? ((await r.json()) as { login: string }[]).map((o) => o.login) : [];
    };
    const [mine, open] = await Promise.all([list('/user/orgs?per_page=100'), list(`/users/${encodeURIComponent(login)}/orgs?per_page=100`)]);
    const orgs = [...new Set([...mine, ...open])].sort().slice(0, PROBE_LIMIT);
    const ok = await Promise.all(orgs.map(async (o) => (await get(`/user/memberships/orgs/${encodeURIComponent(o)}`)).ok));
    return { seen: orgs.filter((_, i) => ok[i]), unseen: orgs.filter((_, i) => !ok[i]) };
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
