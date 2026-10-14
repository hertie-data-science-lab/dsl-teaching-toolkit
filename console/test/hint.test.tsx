// @vitest-environment happy-dom
// The one help pattern (decision 0018): a ? whose popover opens on hover and focus and closes
// on leave, blur and Escape.

import { readFileSync } from 'node:fs';
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
  return { btn: root.querySelector<HTMLButtonElement>('.hint-btn')! };
}

/** The popover as it is now: hidden in place when closed, at the end of the page when open. */
const pop = () => document.querySelector<HTMLElement>('.hint-pop')!;
/** The pointer leaving waits a moment for it to cross to the popover. */
const leaveWait = () => act(() => new Promise((r) => setTimeout(r, 200)));

describe('Hint', () => {
  it('renders a ? described by its closed popover, with Learn more', async () => {
    const { btn } = await mount();
    expect(btn.textContent).toBe('?');
    expect(btn.getAttribute('aria-describedby')).toBe(pop().id);
    expect(pop().hidden).toBe(true);
    expect(pop().textContent).toContain('Dates are in the semester’s timezone.');
    expect(pop().querySelector('a')!.getAttribute('href')).toBe(`${DOCS}07-schedule-releases.md`);
  });

  it('opens on keyboard focus and closes on Escape', async () => {
    const { btn } = await mount();
    await act(() => btn.focus());
    expect(pop().hidden).toBe(false);
    await act(() => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })); });
    expect(pop().hidden).toBe(true);
  });

  it('opens on hover and closes on leave', async () => {
    await mount();
    const wrap = root!.querySelector('.hint-wrap')!;
    await act(() => { wrap.dispatchEvent(new MouseEvent('mouseenter')); });
    expect(pop().hidden).toBe(false);
    await act(() => { wrap.dispatchEvent(new MouseEvent('mouseleave')); });
    await leaveWait();
    expect(pop().hidden).toBe(true);
  });

  it('stays open while hovered after focus leaves, and names the page', async () => {
    const { btn } = await mount();
    expect(btn.getAttribute('aria-label')).toBe('About this page');
    expect(pop().getAttribute('role')).toBeNull();
    const wrap = root!.querySelector('.hint-wrap')!;
    await act(() => btn.focus());
    await act(() => { wrap.dispatchEvent(new MouseEvent('mouseenter')); });
    await act(() => btn.blur());
    expect(pop().hidden).toBe(false);
    await act(() => { wrap.dispatchEvent(new MouseEvent('mouseleave')); });
    await leaveWait();
    expect(pop().hidden).toBe(true);
  });

  it('stays open while focus moves to Learn more, and closes when focus leaves', async () => {
    const { btn } = await mount();
    await act(() => btn.focus());
    await act(() => pop().querySelector('a')!.focus());
    expect(pop().hidden).toBe(false);
    await act(() => (document.activeElement as HTMLElement).blur());
    expect(pop().hidden).toBe(true);
  });

  describe('inside a dialog', () => {
    // The entry sheet: a dialog whose body scrolls, which would clip an absolute popover.
    async function inDialog() {
      root = document.createElement('div');
      document.body.appendChild(root);
      await act(() => render(<div class="entry" role="dialog"><div class="entry-body" style="overflow-y:auto"><label>Release at <Hint small doc="07-schedule-releases.md">Ships the files at this moment.</Hint></label></div></div>, root!));
      return { btn: root.querySelector<HTMLButtonElement>('.hint-btn')! };
    }

    it('fixes the open popover to the viewport, out of the scrolling body’s clip', async () => {
      const { btn } = await inDialog();
      await act(() => btn.focus());
      expect(pop().hidden).toBe(false);
      expect(pop().classList.contains('fixed')).toBe(true);
      expect(pop().style.position).toBe('fixed');
      expect(pop().style.top).toMatch(/px$/);
      expect(pop().style.left).toMatch(/px$/);
      expect(document.body.contains(pop()) && pop().textContent).toContain('Ships the files at this moment.');
      // A fixed box is clipped only by an ancestor that is its containing block: none sets a transform, filter or contain.
      for (let el = pop().parentElement; el; el = el.parentElement) {
        const cs = getComputedStyle(el);
        expect([cs.transform || 'none', cs.filter || 'none', cs.contain || 'none']).toEqual(['none', 'none', 'none']);
      }
    });

    it('keeps the keyboard: Learn more holds it open, Escape closes it', async () => {
      const { btn } = await inDialog();
      await act(() => btn.focus());
      await act(() => pop().querySelector('a')!.focus());
      expect(pop().hidden).toBe(false);
      await act(() => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })); });
      expect(pop().hidden).toBe(true);
      expect(pop().style.position).toBe('');
    });

    it('is fixed at the end of the page outside a dialog too, above everything, and comes back in place closed', async () => {
      const { btn } = await mount();
      await act(() => btn.focus());
      expect(pop().classList.contains('fixed')).toBe(true);
      expect(pop().style.position).toBe('fixed');
      expect(root!.contains(pop())).toBe(false);
      expect(btn.getAttribute('aria-describedby')).toBe(pop().id);
      await act(() => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })); });
      expect(root!.contains(pop())).toBe(true);
      expect(document.querySelector('.hint-host')).toBeNull();
    });

    it('stays open while the pointer crosses from the ? to the popover', async () => {
      await mount();
      const wrap = root!.querySelector('.hint-wrap')!;
      await act(() => { wrap.dispatchEvent(new MouseEvent('mouseenter')); });
      await act(() => { wrap.dispatchEvent(new MouseEvent('mouseleave')); });
      await act(() => { pop().dispatchEvent(new MouseEvent('mouseenter')); });
      await leaveWait();
      expect(pop().hidden).toBe(false);
    });

    it('sits above the modal scrim and the panel, and no dialog box makes itself a fixed box’s containing block', () => {
      const css = readFileSync(`${process.cwd()}/src/styles/console.css`, 'utf8');
      const z = (sel: string) => Number(new RegExp(`(?:^|\\n)${sel.replace(/[.]/g, '\\.')} \\{[^}]*z-index: (\\d+)`).exec(css)?.[1]);
      expect(z('.hint-pop.fixed')).toBeGreaterThan(Math.max(z('.modal-scrim'), z('.drawer')));
      for (const sel of ['.entry', '.entry-body', '.modal', '.drawer-body']) {
        const rules = [...css.matchAll(new RegExp(`(?:^|\\n)\\s*${sel.replace(/[.]/g, '\\.')} \\{([^}]*)\\}`, 'g'))].map((m) => m[1]);
        expect(rules.length).toBeGreaterThan(0);
        for (const r of rules) expect(r).not.toMatch(/transform|filter|contain|will-change/);
      }
    });
  });
});
