// A promise as render state: loading, ready or failed, re-run when its inputs change.

import { useEffect, useState } from 'preact/hooks';

export type Load<T> = { kind: 'loading' } | { kind: 'ready'; value: T } | { kind: 'failed'; error: string };

/** Run `fn` when `deps` change; nothing runs while `fn` is null. */
export function useLoad<T>(fn: (() => Promise<T>) | null, deps: unknown[]): Load<T> {
  const [st, setSt] = useState<Load<T>>({ kind: 'loading' });
  useEffect(() => {
    if (!fn) return;
    let live = true;
    setSt({ kind: 'loading' });
    fn().then(
      (value) => live && setSt({ kind: 'ready', value }),
      (e: unknown) => live && setSt({ kind: 'failed', error: e instanceof Error ? e.message : String(e) }),
    );
    return () => {
      live = false;
    };
  }, [...deps, !!fn]);
  return st;
}
