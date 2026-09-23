import type { GhUser } from '../github/client';

/**
 * How the console gets a token. Today: a pasted classic personal access token (PatAuth).
 * Later: GitHub App user-to-server sign-in (AppAuth, decision 0002) behind the same shape.
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
