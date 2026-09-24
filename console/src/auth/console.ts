import type { GhUser } from '../github/client';
import { AppAuth } from './app';
import { PatAuth } from './pat';
import type { Auth } from './types';

/**
 * The console's one Auth: the GitHub App sign-in when this build has a client id and a
 * relay, and the pasted token always. Whichever signed in answers token() and user().
 */
export class ConsoleAuth implements Auth {
  /** Why the last GitHub sign-in did not finish, for the sign-in screen; null when nothing to say. */
  notice: string | null = null;
  /** Called when a GitHub App session ends by itself (its refresh token was refused). */
  onLost: (() => void) | null = null;
  private active: Auth | null = null;

  constructor(
    readonly pat: PatAuth,
    readonly app: AppAuth | null,
  ) {}

  /** The token path. */
  async signIn(credential?: string): Promise<GhUser> {
    const u = await this.pat.signIn(credential);
    this.active = this.pat;
    return u;
  }

  /** The GitHub App path: leaves for GitHub, and comes back through restore(). */
  signInWithApp(): Promise<GhUser> {
    if (!this.app) return Promise.reject(new Error('GitHub sign-in is not set up for this console.'));
    return this.app.signIn();
  }

  signOut(): void {
    this.app?.signOut();
    this.pat.signOut();
    this.active = null;
  }

  token(): string | null {
    return this.active?.token() ?? null;
  }

  user(): GhUser | null {
    return this.active?.user() ?? null;
  }

  /** Never throws: a failed GitHub sign-in lands in `notice` and the token path is tried. */
  async restore(): Promise<GhUser | null> {
    for (const a of [this.app, this.pat]) {
      if (!a) continue;
      try {
        const u = await a.restore();
        if (u) {
          this.active = a;
          return u;
        }
      } catch (e) {
        this.notice = e instanceof Error ? e.message : String(e);
      }
    }
    return null;
  }
}

/** Build-time settings; the App path shows only when both are set. */
export interface AuthConfig {
  VITE_GH_APP_CLIENT_ID?: string;
  VITE_AUTH_RELAY_URL?: string;
  BASE_URL?: string;
}

/** The browser's ConsoleAuth. Takes a GitHub callback off the URL, so run it before the router reads it. */
export function createAuth(config: AuthConfig = import.meta.env): ConsoleAuth {
  const clientId = config.VITE_GH_APP_CLIENT_ID ?? '';
  const relayUrl = config.VITE_AUTH_RELAY_URL ?? '';
  let auth: ConsoleAuth | null = null;
  const app = clientId && relayUrl
    ? new AppAuth({ clientId, relayUrl, redirectUri: `${location.origin}${config.BASE_URL ?? '/'}`, onLost: () => auth?.onLost?.() })
    : null;
  app?.takeCallback();
  auth = new ConsoleAuth(new PatAuth(), app);
  return auth;
}
