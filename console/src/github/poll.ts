// The one poll loop: an operation's run, the set-up run, a wizard step's checks, the Join
// requests and the fork check all follow GitHub with it, so they share one set of rules.
// It calls nothing while the tab is hidden (and that time does not count towards `maxMs`),
// wakes early when the tab comes back or regains focus, gives up after `maxMisses` failed
// calls in a row, and stops at once when `signal` aborts (the screen went, or sign-out).

export type PollEnd = 'done' | 'stopped' | 'timeout' | 'lost';

export interface PollOptions {
  /** Milliseconds between calls. */
  every: number;
  /** After `after` ms of polling, call every `every` ms instead. */
  backoff?: { after: number; every: number };
  /** Stop after this many ms of polling in a visible tab. */
  maxMs?: number;
  /** Stop after this many failed calls in a row. */
  maxMisses?: number;
  /** Wait one interval before the first call (the caller has just read). */
  later?: boolean;
  signal?: AbortSignal;
  /** Told of each failed call, before the loop decides whether to go on. */
  onMiss?: (e: unknown) => void;
  /** Tests: the pause between calls (default: a timer cut short by focus, the tab coming back, or `signal`). */
  sleep?: (ms: number) => Promise<void>;
  /** Tests: whether the tab is hidden (default `document.hidden`). */
  hidden?: () => boolean;
}

const pageHidden = () => typeof document !== 'undefined' && document.hidden;

/** Wait `ms`, or less: until the window regains focus, the tab becomes visible, or `signal` aborts. */
function pause(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const browser = typeof window !== 'undefined' && typeof document !== 'undefined';
    const done = () => {
      clearTimeout(timer);
      signal?.removeEventListener('abort', done);
      if (browser) {
        window.removeEventListener('focus', wake);
        document.removeEventListener('visibilitychange', wake);
      }
      resolve();
    };
    const wake = () => {
      if (!document.hidden) done();
    };
    const timer = setTimeout(done, ms);
    signal?.addEventListener('abort', done);
    if (browser) {
      window.addEventListener('focus', wake);
      document.addEventListener('visibilitychange', wake);
    }
  });
}

/**
 * Call `fn` until it answers true ('done'), `signal` aborts ('stopped'), `maxMs` of visible
 * polling has passed ('timeout') or `maxMisses` calls in a row have thrown ('lost').
 */
export async function poll(fn: () => Promise<boolean>, o: PollOptions): Promise<PollEnd> {
  const hidden = o.hidden ?? pageHidden;
  const sleep = o.sleep ?? ((ms: number) => pause(ms, o.signal));
  const stopped = () => !!o.signal?.aborted;
  let spent = 0;
  let misses = 0;
  for (let first = !o.later; ; first = false) {
    if (!first) {
      const ms = o.backoff && spent >= o.backoff.after ? o.backoff.every : o.every;
      if (o.maxMs !== undefined && spent + ms >= o.maxMs) return 'timeout';
      spent += ms;
      await sleep(ms);
      while (hidden() && !stopped()) await sleep(o.every);
    }
    if (stopped()) return 'stopped';
    try {
      const over = await fn();
      if (stopped()) return 'stopped';
      if (over) return 'done';
      misses = 0;
    } catch (e) {
      if (stopped()) return 'stopped';
      o.onMiss?.(e);
      if (o.maxMisses !== undefined && ++misses >= o.maxMisses) return 'lost';
    }
  }
}
