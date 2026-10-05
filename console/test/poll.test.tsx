// @vitest-environment happy-dom
// The one poll loop, and the org step that checks again by itself with it: every 10 s and on
// focus, never while a check is in flight, and not at all once every check passes or the step is left.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { poll } from '../src/github/poll';
import { POLL_MS, allOk, useLive, usePoll, type Check } from '../src/wizards/verify';

let root: HTMLElement | null = null;
beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
  vi.useRealTimers();
});

function Step({ fn, open = true }: { fn: () => Promise<Check[]>; open?: boolean }) {
  const live = useLive(fn, []);
  usePoll(live, open && !allOk(live.value));
  return <p>{live.value ? String(allOk(live.value)) : '-'}</p>;
}

async function mount(el: preact.JSX.Element) {
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(() => render(el, root!));
}

const tick = async (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const no: Check[] = [{ text: 'org', ok: false }];
const yes: Check[] = [{ text: 'org', ok: true }];

describe('usePoll', () => {
  it('re-checks every 10 s until every check passes, then stops', async () => {
    let calls = 0;
    const fn = vi.fn(async () => (++calls >= 3 ? yes : no));
    await mount(<Step fn={fn} />);
    expect(fn).toHaveBeenCalledTimes(1);
    await tick(POLL_MS);
    expect(fn).toHaveBeenCalledTimes(2);
    await tick(POLL_MS);
    expect(fn).toHaveBeenCalledTimes(3);
    expect(root!.textContent).toBe('true');
    await tick(POLL_MS * 5);
    expect(fn).toHaveBeenCalledTimes(3);
  });

  it('re-checks on focus, and skips while a check is still in flight', async () => {
    let finish: (v: Check[]) => void = () => {};
    const fn = vi.fn(() => new Promise<Check[]>((r) => (finish = r)));
    await mount(<Step fn={fn} />);
    await tick(POLL_MS * 3);
    expect(fn).toHaveBeenCalledTimes(1);
    await act(async () => finish(no));
    await act(async () => { window.dispatchEvent(new Event('focus')); });
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it('skips while the page is hidden, and checks again when it becomes visible', async () => {
    let hidden = false;
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
    try {
      const fn = vi.fn(async () => no);
      await mount(<Step fn={fn} />);
      hidden = true;
      await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
      await tick(POLL_MS * 3);
      expect(fn).toHaveBeenCalledTimes(1);
      hidden = false;
      await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
      expect(fn).toHaveBeenCalledTimes(2);
    } finally {
      delete (document as { hidden?: boolean }).hidden;
    }
  });

  it('stops when the step is left', async () => {
    const fn = vi.fn(async () => no);
    await mount(<Step fn={fn} />);
    await act(() => render(<Step fn={fn} open={false} />, root!));
    await tick(POLL_MS * 3);
    expect(fn).toHaveBeenCalledTimes(1);
  });
});

describe('poll', () => {
  const instant = async () => {};

  it('calls until done, backs off after `after`, and times out on visible time only', async () => {
    const waits: number[] = [];
    let hidden = false;
    let calls = 0;
    const end = await poll(async () => {
      calls++;
      if (calls === 2) hidden = true;
      return false;
    }, {
      every: 10, backoff: { after: 30, every: 20 }, maxMs: 70,
      sleep: async (ms) => {
        waits.push(ms);
        if (hidden && waits.length > 4) hidden = false;
      },
      hidden: () => hidden,
    });
    expect(end).toBe('timeout');
    // 10, then 10 + three hidden waits (not counted), then 10, 20 and the cap at 70.
    expect(waits).toEqual([10, 10, 10, 10, 10, 10, 20]);
    expect(calls).toBe(5);
  });

  it('gives up after maxMisses failures in a row, a success resetting the count', async () => {
    const answers = [false, true, false, false];
    const misses: unknown[] = [];
    const end = await poll(async () => {
      if (!answers.shift()) throw new Error('offline');
      return false;
    }, { every: 0, maxMisses: 2, sleep: instant, hidden: () => false, onMiss: (e) => misses.push(e) });
    expect(end).toBe('lost');
    expect(misses).toHaveLength(3);
  });

  it('stops when the signal aborts, mid-wait included', async () => {
    const stop = new AbortController();
    let calls = 0;
    const going = poll(async () => (calls++, false), { every: 60_000, signal: stop.signal, hidden: () => false });
    await vi.advanceTimersByTimeAsync(0);
    stop.abort();
    expect(await going).toBe('stopped');
    expect(calls).toBe(1);
  });
});
