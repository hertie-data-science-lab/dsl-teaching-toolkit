// A vertical resize handle (the Files panel's splitter, the marks grid's column grips): drag with
// the pointer (mouse, pen or touch, captured), Left/Right arrows to step, double-click (and Home,
// where asked) to reset; a separator for assistive tech.

import { useState } from 'preact/hooks';

export interface DragHandle {
  /** Pointer down on `el`: what each move does and what the release does, or null to ignore it. */
  start: (e: PointerEvent, el: HTMLElement) => { move: (ev: PointerEvent) => void; end?: () => void } | null;
  /** An arrow key: -1 left, +1 right. */
  step: (dir: -1 | 1, el: HTMLElement) => void;
  /** A double-click, and Home with `home`. */
  reset: () => void;
  home?: boolean;
}

/** The handle's props, and whether it is being dragged. */
export function useDragHandle(h: DragHandle) {
  const [dragging, setDragging] = useState(false);
  const onPointerDown = (e: PointerEvent) => {
    const el = e.currentTarget as HTMLElement;
    const d = h.start(e, el);
    if (!d) return;
    e.preventDefault();
    el.setPointerCapture?.(e.pointerId);
    setDragging(true);
    const end = () => {
      setDragging(false);
      el.removeEventListener('pointermove', d.move);
      el.removeEventListener('pointerup', end);
      el.removeEventListener('pointercancel', end);
      d.end?.();
    };
    el.addEventListener('pointermove', d.move);
    el.addEventListener('pointerup', end);
    el.addEventListener('pointercancel', end);
  };
  const onKeyDown = (e: KeyboardEvent) => {
    const dir = e.key === 'ArrowLeft' ? -1 : e.key === 'ArrowRight' ? 1 : 0;
    if (dir) h.step(dir, e.currentTarget as HTMLElement);
    else if (e.key === 'Home' && h.home) h.reset();
    else return;
    e.preventDefault();
  };
  return {
    dragging,
    props: { role: 'separator' as const, 'aria-orientation': 'vertical' as const, tabIndex: 0, title: 'Drag to resize; double-click to reset', onPointerDown, onKeyDown, onDblClick: () => h.reset() },
  };
}
