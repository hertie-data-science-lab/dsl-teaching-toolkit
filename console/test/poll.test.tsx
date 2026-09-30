// @vitest-environment happy-dom
// The org step checks again by itself: every 10 s and on focus, never while a check is in
// flight, and not at all once every check passes or the step is left.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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
