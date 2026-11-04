// @vitest-environment happy-dom
// The Files panel's draggable bar (plan 2026-11-04, decision 8): the columns' ratio held inside
// 25-75%, stepped by the arrow keys, reset by Home and a double-click, dragged by the pointer,
// and the From / To row laid out in the same columns.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, describe, expect, it } from 'vitest';
import { SplitPanes, clampRatio } from '../src/ui/Splitter';

let root: HTMLElement | null = null;
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
});

async function mount() {
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(() => render(<SplitPanes label="course-materials" left={<p>tree</p>} right={<p>file</p>} heads={[<span>From</span>, <span>To</span>]} />, root!));
  const bar = root.querySelector<HTMLElement>('.splitter')!;
  const lf = () => root!.querySelector<HTMLElement>('.split-panes')!.style.getPropertyValue('--lf');
  const key = (k: string) => act(() => { bar.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true })); });
  return { el: root, bar, lf, key };
}

describe('the splitter', () => {
  it('holds a ratio inside 25-75%', () => {
    expect(clampRatio(0.1)).toBe(0.25);
    expect(clampRatio(0.9)).toBe(0.75);
    expect(clampRatio(0.4)).toBe(0.4);
  });

  it('lays out the heads, the tree, the bar and the right column', async () => {
    const { el, bar } = await mount();
    expect([...el.querySelector('.split-heads')!.children].map((c) => c.className)).toEqual(['rh-row', 'split-gap', 'rh-row']);
    expect(el.querySelector('nav.materials-tree')!.getAttribute('aria-label')).toBe('course-materials');
    expect(bar.getAttribute('role')).toBe('separator');
    expect(bar.getAttribute('aria-valuenow')).toBe('55');
  });

  it('steps with the arrow keys, stops at the bounds, resets with Home and a double-click', async () => {
    const { bar, lf, key } = await mount();
    expect(lf()).toBe('55fr');
    await key('ArrowRight');
    expect(lf()).toBe('60fr');
    for (let i = 0; i < 6; i++) await key('ArrowRight');
    expect(lf()).toBe('75fr');
    expect(bar.getAttribute('aria-valuenow')).toBe('75');
    await key('Home');
    expect(lf()).toBe('55fr');
    for (let i = 0; i < 8; i++) await key('ArrowLeft');
    expect(lf()).toBe('25fr');
    await act(() => { bar.dispatchEvent(new MouseEvent('dblclick', { bubbles: true })); });
    expect(lf()).toBe('55fr');
  });

  it('follows the pointer while dragged', async () => {
    const { el, bar, lf } = await mount();
    const box = el.querySelector<HTMLElement>('.files-split:not(.split-heads)')!;
    box.getBoundingClientRect = () => ({ left: 100, width: 1000, top: 0, height: 600, right: 1100, bottom: 600, x: 100, y: 0, toJSON: () => ({}) });
    await act(() => { bar.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, clientX: 650, pointerId: 1 })); });
    expect(bar.classList.contains('dragging')).toBe(true);
    await act(() => { bar.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, clientX: 400, pointerId: 1 })); });
    expect(lf()).toBe('30fr');
    await act(() => { bar.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 1 })); });
    expect(bar.classList.contains('dragging')).toBe(false);
  });
});
