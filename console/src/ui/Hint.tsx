// The one help pattern (decision 0018): a small round `?` whose popover opens on hover and on
// keyboard focus, and closes on leave, blur or Escape. No click state: a tap focuses it. Its
// wrapper is `.hint-wrap`: `p.hint` is a form field's note, and must not take its layout.
// Inside a dialog (the schedule's entry sheet, a modal, the operation panel) the body scrolls and
// would clip it, so there it is fixed to the viewport beside its ?, and follows it on scroll.

import type { ComponentChildren } from 'preact';
import { useEffect, useId, useLayoutEffect, useRef, useState } from 'preact/hooks';
import { DOCS } from './bits';

const GAP = 8; // between the ? and the popover, and the popover and the viewport's edge

/** The boxes whose scrolling body would clip a popover placed inside it. */
const BOXED = '[role="dialog"], .drawer';

/** `small` sets it beside a fact in a list rather than a heading. */
export function Hint({ children, doc, label = 'About this page', small = false }: { children: ComponentChildren; doc?: string; label?: string; small?: boolean }) {
  const [hover, setHover] = useState(false);
  const [focus, setFocus] = useState(false);
  const [place, setPlace] = useState<{ cls: string; maxWidth?: number; at?: { top: number; left: number } }>({ cls: '' });
  const [moved, setMoved] = useState(0);
  const open = hover || focus;
  const id = useId();
  const wrap = useRef<HTMLSpanElement>(null);
  const pop = useRef<HTMLSpanElement>(null);

  // Below-left by default. Flipped right when it would pass the left edge (and then narrowed so
  // it cannot pass the right one), and above when it does not fit below but fits above.
  // In a dialog the same rules give viewport coordinates for a fixed popover.
  useLayoutEffect(() => {
    if (!open || !pop.current || !wrap.current) return setPlace({ cls: '' });
    const r = pop.current.getBoundingClientRect();
    const b = wrap.current.getBoundingClientRect();
    if (wrap.current.closest(BOXED)) {
      const up = b.bottom + GAP + r.height > window.innerHeight && b.top - GAP >= r.height;
      const top = up ? b.top - GAP - r.height : b.bottom + GAP;
      return setPlace({ cls: ` fixed${up ? ' flip-y' : ''}`, at: { top, left: Math.max(GAP, b.right - r.width) } });
    }
    const flipX = r.left < 0;
    const flipY = r.bottom > window.innerHeight && b.top - GAP >= r.height;
    const room = window.innerWidth - b.left - GAP;
    setPlace({ cls: `${flipX ? ' flip-x' : ''}${flipY ? ' flip-y' : ''}`, maxWidth: flipX && r.width > room ? room : undefined });
  }, [open, moved]);

  // A fixed popover follows its ? when the dialog's body (or the page) scrolls or resizes.
  useEffect(() => {
    if (!open || !place.at) return;
    const follow = () => setMoved((n) => n + 1);
    window.addEventListener('scroll', follow, true);
    window.addEventListener('resize', follow);
    return () => {
      window.removeEventListener('scroll', follow, true);
      window.removeEventListener('resize', follow);
    };
  }, [open, !!place.at]);

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
    <span class={small ? 'hint-wrap small' : 'hint-wrap'} ref={wrap} onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)} onFocusIn={() => setFocus(true)} onFocusOut={blur}>
      <button class="hint-btn" type="button" aria-label={label} aria-describedby={id}>?</button>
      <span class={`hint-pop${place.cls}`} id={id} ref={pop} hidden={!open} style={place.at ? { position: 'fixed', top: `${place.at.top}px`, left: `${place.at.left}px`, right: 'auto', bottom: 'auto' } : place.maxWidth ? { maxWidth: `${place.maxWidth}px` } : undefined}>
        {children}
        {doc ? <> <a href={`${DOCS}${doc}`} target="_blank" rel="noopener">Learn more</a></> : null}
      </span>
    </span>
  );
}
