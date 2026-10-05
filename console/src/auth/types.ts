import { GitHubError, type GhUser } from '../github/client';

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
  /**
   * Re-establish a session remembered earlier in this tab, if any (`untilAnswered`: kept and
   * tried again while GitHub does not answer, `onRetry` told each time).
   */
  restore(onRetry?: () => void): Promise<GhUser | null>;
}

export class SignInError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'SignInError';
  }
}

/** The waits between reload checks GitHub did not answer; the last repeats. */
export const RETRY_MS = [2000, 5000, 10000, 30000];

/** GitHub (or the relay) answered and refused: the saved sign-in is over. Anything else is no answer. */
const refused = (e: unknown) => e instanceof SignInError || (e instanceof GitHubError && (e.status === 401 || e.status === 403));

/**
 * The one reload policy for both sign-ins: run `attempt` until it gets an answer. Refused:
 * null, and the caller forgets the saved session. No answer (offline, a 5xx): `onRetry` told,
 * a wait from RETRY_MS, and again.
 */
export async function untilAnswered(attempt: () => Promise<GhUser>, onRetry: (() => void) | undefined, sleep: (ms: number) => Promise<void>): Promise<GhUser | null> {
  for (let i = 0; ; i++) {
    try {
      return await attempt();
    } catch (e) {
      if (refused(e)) return null;
      onRetry?.();
      await sleep(RETRY_MS[Math.min(i, RETRY_MS.length - 1)]);
    }
  }
}

/**
 * The subset of Storage the console uses: localStorage or sessionStorage in the browser, a Map
 * in tests. Only a sweep over every key (forgetStudentPrefs) needs `length` and `key`.
 */
export interface KeyStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
  removeItem?(key: string): void;
  readonly length?: number;
  key?(index: number): string | null;
}

/**
 * The browser's localStorage or sessionStorage, or null where it is missing or refused (a
 * private window, blocked site data). With the helpers below, the one place storage failures
 * are caught: a read that cannot happen is null, a write that cannot happen is false.
 */
export function safeStorage(kind: 'local' | 'session'): KeyStore | null {
  try {
    return (kind === 'local' ? globalThis.localStorage : globalThis.sessionStorage) ?? null;
  } catch {
    return null;
  }
}

export function readText(store: KeyStore | null, key: string): string | null {
  try {
    return store?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

export function writeText(store: KeyStore | null, key: string, value: string): boolean {
  try {
    if (!store) return false;
    store.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

/** `key`'s value parsed as JSON; null when absent, unreadable or not JSON. */
export function readJson<T>(store: KeyStore | null, key: string): T | null {
  const raw = readText(store, key);
  try {
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

export const writeJson = (store: KeyStore | null, key: string, value: unknown): boolean => writeText(store, key, JSON.stringify(value));

export function remove(store: KeyStore | null, key: string): void {
  try {
    store?.removeItem?.(key);
  } catch {
    /* storage unavailable: nothing was kept */
  }
}
