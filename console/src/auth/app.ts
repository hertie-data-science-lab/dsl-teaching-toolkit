import type { GhUser } from '../github/client';
import type { Auth } from './types';

/**
 * GitHub App user-to-server sign-in. Not built yet.
 *
 * TODO(decision 0002): register "DSL Teaching Toolkit Console" with callback
 * https://hertie-data-science-lab.github.io/dsl-teaching-toolkit/auth/callback and implement
 * the device or web flow here, behind the same Auth interface PatAuth implements.
 */
export class AppAuth implements Auth {
  signIn(): Promise<GhUser> {
    return Promise.reject(new Error('GitHub App sign-in is not available yet (decision 0002).'));
  }
  signOut(): void {}
  token(): string | null {
    return null;
  }
  user(): GhUser | null {
    return null;
  }
  restore(): Promise<GhUser | null> {
    return Promise.resolve(null);
  }
}
