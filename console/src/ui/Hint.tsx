// The one help pattern (decision 0018): a small round `?` whose popover opens on hover and on
// keyboard focus, and closes on leave, blur or Escape. No click state: a tap focuses it.

import type { ComponentChildren } from 'preact';
import { useEffect, useId, useLayoutEffect, useRef, useState } from 'preact/hooks';
import { DOCS } from './bits';

export function Hint({ children, doc, label = 'Help' }: { children: ComponentChildren; doc?: string; label?: string }) {
  const [open, setOpen] = useState(false);
  const [flip, setFlip] = useState('');
  const id = useId();
  const wrap = useRef<HTMLSpanElement>(null);
  const pop = useRef<HTMLSpanElement>(null);

  // Below-left by default; flipped on either axis when it would leave the viewport.
  useLayoutEffect(() => {
    if (!open || !pop.current) return setFlip('');
    const r = pop.current.getBoundingClientRect();
    setFlip(`${r.left < 0 ? ' flip-x' : ''}${r.bottom > window.innerHeight && r.height < r.top ? ' flip-y' : ''}`);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    document.addEventListener('keydown', esc);
    return () => document.removeEventListener('keydown', esc);
  }, [open]);

  const blur = (e: FocusEvent) => {
    if (!wrap.current?.contains(e.relatedTarget as Node | null)) setOpen(false);
  };
  return (
    <span class="hint" ref={wrap} onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)} onFocusIn={() => setOpen(true)} onFocusOut={blur}>
      <button class="hint-btn" type="button" aria-label={label} aria-describedby={id}>?</button>
      <span class={`hint-pop${flip}`} id={id} role="tooltip" ref={pop} hidden={!open}>
        {children}
        {doc ? <> <a href={`${DOCS}${doc}`} target="_blank" rel="noopener">Learn more</a></> : null}
      </span>
    </span>
  );
}
