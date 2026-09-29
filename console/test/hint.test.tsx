// @vitest-environment happy-dom
// The one help pattern (decision 0018): a ? whose popover opens on hover and focus and closes
// on leave, blur and Escape.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, describe, expect, it } from 'vitest';
import { DOCS } from '../src/ui/bits';
import { Hint } from '../src/ui/Hint';

let root: HTMLElement | null = null;
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
});

async function mount() {
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(() => render(<h1>Schedule <Hint doc="07-schedule-releases.md">Dates are in the semester’s timezone.</Hint></h1>, root!));
  return { btn: root.querySelector<HTMLButtonElement>('.hint-btn')!, pop: root.querySelector<HTMLElement>('.hint-pop')! };
}

describe('Hint', () => {
  it('renders a ? described by its closed popover, with Learn more', async () => {
    const { btn, pop } = await mount();
    expect(btn.textContent).toBe('?');
    expect(btn.getAttribute('aria-describedby')).toBe(pop.id);
    expect(pop.hidden).toBe(true);
    expect(pop.textContent).toContain('Dates are in the semester’s timezone.');
    expect(pop.querySelector('a')!.getAttribute('href')).toBe(`${DOCS}07-schedule-releases.md`);
  });

  it('opens on keyboard focus and closes on Escape', async () => {
    const { btn, pop } = await mount();
    await act(() => btn.focus());
    expect(pop.hidden).toBe(false);
    await act(() => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })); });
    expect(pop.hidden).toBe(true);
  });

  it('opens on hover and closes on leave', async () => {
    const { pop } = await mount();
    const wrap = root!.querySelector('.hint')!;
    await act(() => { wrap.dispatchEvent(new MouseEvent('mouseenter')); });
    expect(pop.hidden).toBe(false);
    await act(() => { wrap.dispatchEvent(new MouseEvent('mouseleave')); });
    expect(pop.hidden).toBe(true);
  });

  it('stays open while focus moves to Learn more, and closes when focus leaves', async () => {
    const { btn, pop } = await mount();
    await act(() => btn.focus());
    await act(() => pop.querySelector('a')!.focus());
    expect(pop.hidden).toBe(false);
    await act(() => (document.activeElement as HTMLElement).blur());
    expect(pop.hidden).toBe(true);
  });
});
