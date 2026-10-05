// A wizard's unfinished answers, kept in this browser so leaving (to create an org on
// GitHub, say) loses nothing. Only answers not yet written anywhere live here; each finished
// step has written its file or run its operation, and the wizard re-reads those live. Storage
// can be missing or refuse (a private window), so every read and write is guarded and the
// wizard works without it.

import { useState } from 'preact/hooks';
import { readJson, remove, safeStorage, writeJson } from '../auth/types';

const PREFIX = 'dsl-console:wizard:';
const store = () => safeStorage('local');

export function loadDraft<T extends object>(key: string, fallback: T): T {
  const v = readJson<Partial<T>>(store(), PREFIX + key);
  return v && typeof v === 'object' ? { ...fallback, ...v } : fallback;
}

/** Refused storage: the wizard still works; it just will not remember. */
export function saveDraft(key: string, value: object | null): void {
  if (value === null) remove(store(), PREFIX + key);
  else writeJson(store(), PREFIX + key, value);
}

/** A draft held in state and mirrored to storage; `clear` forgets it once the wizard is done. */
export function useDraft<T extends object>(key: string, fallback: () => T): [T, (patch: Partial<T>) => void, () => void] {
  const [d, setD] = useState<{ key: string; v: T }>(() => ({ key, v: loadDraft(key, fallback()) }));
  const cur = d.key === key ? d.v : loadDraft(key, fallback());
  const set = (patch: Partial<T>) => {
    const v = { ...cur, ...patch };
    setD({ key, v });
    saveDraft(key, v);
  };
  const clear = () => {
    saveDraft(key, null);
    setD({ key, v: fallback() });
  };
  return [cur, set, clear];
}

const INSTALL_RETURN = `${PREFIX}install-return`;
/** How long a remembered step stays good: an install is minutes, not days. */
export const INSTALL_RETURN_MS = 60 * 60 * 1000;

/** The wizard step (`?course=x#new-semester-1`) to reopen when GitHub sends the person back from installing the app. */
export function rememberInstallReturn(where: string, now = Date.now()): void {
  writeJson(store(), INSTALL_RETURN, { where, at: now }); // refused: the return lands on New course instead
}

/** The remembered step, forgotten as it is read; null when there is none or it is over an hour old. */
export function takeInstallReturn(now = Date.now()): string | null {
  const v = readJson<{ where?: unknown; at?: unknown }>(store(), INSTALL_RETURN);
  remove(store(), INSTALL_RETURN);
  return v && typeof v.where === 'string' && typeof v.at === 'number' && now - v.at <= INSTALL_RETURN_MS ? v.where : null;
}
