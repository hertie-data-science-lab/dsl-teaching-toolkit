// The console's one modal dialog: the semester defaults form, the set-aside question.

import type { ComponentChildren } from 'preact';
import { useEffect, useRef } from 'preact/hooks';

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** A modal dialog: Escape and a click on the scrim close it; focus moves in and Tab stays inside. */
export function Modal({ title, onClose, children, small = false }: { title: string; onClose: () => void; children: ComponentChildren; small?: boolean }) {
  const box = useRef<HTMLDivElement>(null);
  const downOnScrim = useRef(false);
  useEffect(() => {
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
    return () => document.removeEventListener('keydown', key);
  }, []);
  return (
    <div class="modal-scrim" onMouseDown={(e) => (downOnScrim.current = e.target === e.currentTarget)} onClick={(e) => downOnScrim.current && e.target === e.currentTarget && onClose()}>
      <div class={small ? 'modal small' : 'modal'} role="dialog" aria-modal="true" aria-labelledby="modal-title" tabindex={-1} ref={box}>
        <div class="entry-head"><h2 id="modal-title">{title}</h2><button class="x" type="button" aria-label="Close" onClick={onClose}>&times;</button></div>
        <div class="entry-body">{children}</div>
      </div>
    </div>
  );
}
