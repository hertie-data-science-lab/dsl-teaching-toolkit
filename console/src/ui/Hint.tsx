// The one help pattern (decision 0018): a small round `?` whose popover opens on hover and on
// keyboard focus, and closes on leave, blur or Escape. No click state: a tap focuses it. Its
// wrapper is `.hint-wrap`: `p.hint` is a form field's note, and must not take its layout.
// An open popover is rendered at the end of the page (a portal to body) and fixed to the viewport
// beside its ?, clamped inside it, above everything: no scrolling body, sticky header, panel or
// dialog can clip or cover it. It follows its ? on scroll. Closed, it waits hidden in place.

import { render, type ComponentChildren } from 'preact';
import { useEffect, useId, useLayoutEffect, useRef, useState } from 'preact/hooks';
import { DOCS } from './bits';

const GAP = 8; // between the ? and the popover, and the popover and the viewport's edge
const LEAVE_MS = 150; // how long a popover waits for the pointer to cross from its ?

/** `small` sets it beside a fact in a list or a column header rather than a heading. */
export function Hint({ children, doc, label = 'About this page', small = false }: { children: ComponentChildren; doc?: string; label?: string; small?: boolean }) {
  const [hover, setHover] = useState(false);
  const [focus, setFocus] = useState(false);
  const [place, setPlace] = useState<{ up: boolean; top: number; left: number } | null>(null);
  const open = hover || focus;
  const id = useId();
  const wrap = useRef<HTMLSpanElement>(null);
  const pop = useRef<HTMLSpanElement>(null);
  // Leaving the ? for the popover crosses a gap: closing waits a moment for the pointer to arrive.
  const leaving = useRef<ReturnType<typeof setTimeout> | null>(null);
  const enter = () => {
    if (leaving.current) clearTimeout(leaving.current);
    leaving.current = null;
    setHover(true);
  };
  const leave = () => {
    if (leaving.current) clearTimeout(leaving.current);
    leaving.current = setTimeout(() => {
      leaving.current = null;
      setHover(false);
    }, LEAVE_MS);
  };
  useEffect(() => () => void (leaving.current && clearTimeout(leaving.current)), []);

  // While open, the popover lives in its own box at the end of the page (preact/compat's portal
  // would change how every component renders, so the box is rendered into by hand).
  const [host, setHost] = useState<HTMLElement | null>(null);
  useLayoutEffect(() => {
    if (!open) return;
    const box = document.createElement('div');
    box.className = 'hint-host';
    document.body.appendChild(box);
    setHost(box);
    return () => {
      render(null, box);
      box.remove();
      setHost(null);
      setPlace(null);
    };
  }, [open]);

  // Below the ?, right-aligned to it; above when it does not fit below but fits above; then
  // clamped so it never passes either edge of the viewport. Measured once the box holds it, and
  // again on scroll or resize; an unchanged place renders nothing.
  const measure = () => {
    if (!pop.current || !wrap.current) return;
    const r = pop.current.getBoundingClientRect();
    const b = wrap.current.getBoundingClientRect();
    const up = b.bottom + GAP + r.height > window.innerHeight && b.top - GAP >= r.height;
    const left = Math.max(GAP, Math.min(b.right - r.width, window.innerWidth - GAP - r.width));
    const next = { up, top: up ? b.top - GAP - r.height : b.bottom + GAP, left };
    setPlace((prev) => (prev && prev.up === next.up && prev.top === next.top && prev.left === next.left ? prev : next));
  };

  // The popover follows its ? when anything scrolls or the window resizes.
  useEffect(() => {
    if (!open) return;
    window.addEventListener('scroll', measure, true);
    window.addEventListener('resize', measure);
    return () => {
      window.removeEventListener('scroll', measure, true);
      window.removeEventListener('resize', measure);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const esc = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (leaving.current) clearTimeout(leaving.current);
      setHover(false);
      setFocus(false);
    };
    document.addEventListener('keydown', esc);
    return () => document.removeEventListener('keydown', esc);
  }, [open]);

  // Focus moving between the ? and the popover's Learn more keeps it open.
  const blur = (e: FocusEvent) => {
    const to = e.relatedTarget as Node | null;
    if (!wrap.current?.contains(to) && !pop.current?.contains(to)) setFocus(false);
  };
  const body = (
    <span
      class={`hint-pop${place?.up ? ' flip-y' : ''}`}
      id={id}
      ref={pop}
      hidden={!open}
      style={open ? { position: 'fixed', top: `${place?.top ?? 0}px`, left: `${place?.left ?? 0}px`, visibility: place ? undefined : 'hidden' } : undefined}
      onMouseEnter={enter}
      onMouseLeave={leave}
      onFocusIn={() => setFocus(true)}
      onFocusOut={blur}
    >
      {children}
      {doc ? <> <a href={`${DOCS}${doc}`} target="_blank" rel="noopener">Learn more</a></> : null}
    </span>
  );
  useLayoutEffect(() => {
    if (host) render(body, host);
  });
  // Once on opening, after the box holds it (the effect above runs first).
  useLayoutEffect(() => {
    if (host) measure();
  }, [host]);
  return (
    <span class={small ? 'hint-wrap small' : 'hint-wrap'} ref={wrap} onMouseEnter={enter} onMouseLeave={leave} onFocusIn={() => setFocus(true)} onFocusOut={blur}>
      <button class="hint-btn" type="button" aria-label={label} aria-describedby={id}>?</button>
      {open ? null : body}
    </span>
  );
}
