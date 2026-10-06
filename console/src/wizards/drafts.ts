// A wizard's unfinished answers, kept in this browser so leaving (to create an org on
// GitHub, say) loses nothing. Only answers not yet written anywhere live here; each finished
// step has written its file or run its operation, and the wizard re-reads those live. Storage
// can be missing or refuse (a private window), so every read and write is guarded and the
// wizard works without it.

import { useState } from 'preact/hooks';

const PREFIX = 'dsl-console:wizard:';

function store(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage;
  } catch {
    return null;
  }
}

export function loadDraft<T extends object>(key: string, fallback: T): T {
  try {
    const raw = store()?.getItem(PREFIX + key);
    return raw ? { ...fallback, ...(JSON.parse(raw) as Partial<T>) } : fallback;
  } catch {
    return fallback;
  }
}

export function saveDraft(key: string, value: object | null): void {
  try {
    if (value === null) store()?.removeItem(PREFIX + key);
    else store()?.setItem(PREFIX + key, JSON.stringify(value));
  } catch {
    /* the wizard still works; it just will not remember */
  }
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
