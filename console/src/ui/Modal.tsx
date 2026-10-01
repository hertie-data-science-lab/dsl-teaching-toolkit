// The console's one modal dialog: the semester defaults form, the set-aside question.

import type { ComponentChildren } from 'preact';
import { useEffect, useRef } from 'preact/hooks';

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** The body paragraph a small dialog is described by (`aria-describedby`). */
export const MODAL_TEXT_ID = 'modal-text';

/**
 * A modal dialog: Escape and a click on the scrim close it; focus moves in and Tab stays inside.
 * On close focus goes back to what had it when the dialog opened, or, when that has left the page
 * (the line it was on moved), to `fallback()`. A `small` dialog carries its own Close button, so
 * its × is hidden from assistive tech, and it is described by its `MODAL_TEXT_ID` paragraph.
 */
export function Modal({ title, onClose, children, small = false, fallback }: { title: string; onClose: () => void; children: ComponentChildren; small?: boolean; fallback?: () => HTMLElement | null | undefined }) {
  const box = useRef<HTMLDivElement>(null);
  const downOnScrim = useRef(false);
  const back = useRef(fallback);
  back.current = fallback;
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    box.current?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') return onClose();
      if (e.key !== 'Tab' || !box.current) return;
      const all = [...box.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
      if (!all.length) return;
      const first = all[0], last = all[all.length - 1], at = document.activeElement;
      if (e.shiftKey && (at === first || at === box.current)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (at === last || !box.current.contains(at))) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', key);
    return () => {
      document.removeEventListener('keydown', key);
      // After the render that closed it, so a moved line's new place is on the page.
      queueMicrotask(() => (opener?.isConnected ? opener : back.current?.())?.focus());
    };
  }, []);
  return (
    <div class="modal-scrim" onMouseDown={(e) => (downOnScrim.current = e.target === e.currentTarget)} onClick={(e) => downOnScrim.current && e.target === e.currentTarget && onClose()}>
      <div class={small ? 'modal small' : 'modal'} role="dialog" aria-modal="true" aria-labelledby="modal-title" aria-describedby={small ? MODAL_TEXT_ID : undefined} tabindex={-1} ref={box}>
        <div class="entry-head">
          <h2 id="modal-title">{title}</h2>
          {small ? <button class="x" type="button" aria-hidden="true" tabindex={-1} onClick={onClose}>&times;</button> : <button class="x" type="button" aria-label="Close" onClick={onClose}>&times;</button>}
        </div>
        <div class="entry-body">{children}</div>
      </div>
    </div>
  );
}
