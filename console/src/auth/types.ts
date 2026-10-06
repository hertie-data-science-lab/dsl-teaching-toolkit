import type { GhUser } from '../github/client';

/**
 * How the console gets a token: GitHub App sign-in through the relay (AppAuth, decision
 * 0002), or a pasted personal access token (PatAuth), the no-server fallback. ConsoleAuth
 * holds both and answers for whichever signed in.
 */
export interface Auth {
  /** Validate the credential and remember it for this browser tab. */
  signIn(credential?: string): Promise<GhUser>;
  signOut(): void;
  token(): string | null;
  user(): GhUser | null;
  /** Re-establish a session remembered earlier in this tab, if any. */
  restore(): Promise<GhUser | null>;
}

export class SignInError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'SignInError';
  }
}

/** The subset of Storage the sign-ins need; sessionStorage in the browser, a Map in tests. */
export interface TokenStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem(key: string): void;
}

export function browserStore(): TokenStore | null {
  try {
    return globalThis.sessionStorage ?? null;
  } catch {
    return null;
  }
}
