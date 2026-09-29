// The one help pattern (decision 0018): a small round `?` whose popover opens on hover and on
// keyboard focus, and closes on leave, blur or Escape. No click state: a tap focuses it.

import type { ComponentChildren } from 'preact';
import { useEffect, useId, useLayoutEffect, useRef, useState } from 'preact/hooks';
import { DOCS } from './bits';

const GAP = 8; // between the ? and the popover, and the popover and the viewport's edge

export function Hint({ children, doc, label = 'About this page' }: { children: ComponentChildren; doc?: string; label?: string }) {
  const [hover, setHover] = useState(false);
  const [focus, setFocus] = useState(false);
  const [place, setPlace] = useState<{ cls: string; maxWidth?: number }>({ cls: '' });
  const open = hover || focus;
  const id = useId();
  const wrap = useRef<HTMLSpanElement>(null);
  const pop = useRef<HTMLSpanElement>(null);

  // Below-left by default. Flipped right when it would pass the left edge (and then narrowed so
  // it cannot pass the right one), and above when it does not fit below but fits above.
  useLayoutEffect(() => {
    if (!open || !pop.current || !wrap.current) return setPlace({ cls: '' });
    const r = pop.current.getBoundingClientRect();
    const b = wrap.current.getBoundingClientRect();
    const flipX = r.left < 0;
    const flipY = r.bottom > window.innerHeight && b.top - GAP >= r.height;
    const room = window.innerWidth - b.left - GAP;
    setPlace({ cls: `${flipX ? ' flip-x' : ''}${flipY ? ' flip-y' : ''}`, maxWidth: flipX && r.width > room ? room : undefined });
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const esc = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      setHover(false);
      setFocus(false);
    };
    document.addEventListener('keydown', esc);
    return () => document.removeEventListener('keydown', esc);
  }, [open]);

  const blur = (e: FocusEvent) => {
    if (!wrap.current?.contains(e.relatedTarget as Node | null)) setFocus(false);
  };
  return (
    <span class="hint" ref={wrap} onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)} onFocusIn={() => setFocus(true)} onFocusOut={blur}>
      <button class="hint-btn" type="button" aria-label={label} aria-describedby={id}>?</button>
      <span class={`hint-pop${place.cls}`} id={id} ref={pop} hidden={!open} style={place.maxWidth ? { maxWidth: `${place.maxWidth}px` } : undefined}>
        {children}
        {doc ? <> <a href={`${DOCS}${doc}`} target="_blank" rel="noopener">Learn more</a></> : null}
      </span>
    </span>
  );
}
