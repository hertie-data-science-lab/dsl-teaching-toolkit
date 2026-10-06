// What a screen needs to change anything: the signed-in user's client, the operation
// panel, the status store (to re-read after a write) and the file cache. Provided by the
// app; absent in a render with no one signed in, where every Save and operation is inert.

import { createContext } from 'preact';
import { useContext } from 'preact/hooks';
import type { GhUser, GitHubClient } from './github/client';
import type { Files } from './model/files';
import type { StatusStore } from './model/status';
import type { OpsSession } from './ops/session';

export interface Env {
  client: GitHubClient;
  user: GhUser;
  ops: OpsSession;
  statuses: StatusStore;
  files: Files;
  /** Read the course list again (a wizard just made a course or a semester). */
  rediscover?: () => Promise<void>;
  /** How long to wait between reads of a commit's checks (tests pass 0). */
  pollMs?: number;
}

export const EnvCtx = createContext<Env | null>(null);

export function useEnv(): Env | null {
  return useContext(EnvCtx);
}
